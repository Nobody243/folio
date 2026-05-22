import os
import sys
import time
import sqlite3
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json, percentile_approx, when, lower, trim, lit

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "raw_listings")
DB_PATH = "/app/data/products.db"

# ── Wait for Kafka topic to exist ──────────────────────────────────────────────
# The topic is auto-created when the backend sends the first message, but Spark
# may start before that happens. We retry for up to 120 s so the container
# doesn't crash immediately.

def wait_for_topic(bootstrap, topic, timeout=120):
    """Block until *topic* is available on the Kafka cluster."""
    try:
        from kafka import KafkaConsumer
        from kafka.errors import NoBrokersAvailable
    except ImportError:
        # kafka-python not installed inside the Spark image —
        # fall back to a simple socket check + hope for the best.
        print(f"[WARN] kafka-python not available; sleeping 30 s then continuing…")
        time.sleep(30)
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            consumer = KafkaConsumer(
                bootstrap_servers=[bootstrap],
                request_timeout_ms=5000,
            )
            topics = consumer.topics()
            consumer.close()
            if topic in topics:
                print(f"[INFO] Kafka topic '{topic}' is available.")
                return
            print(f"[INFO] Topic '{topic}' not found yet ({sorted(topics)}). Retrying…")
        except NoBrokersAvailable:
            print(f"[INFO] Kafka broker not reachable yet. Retrying in 5 s…")
        except Exception as e:
            print(f"[WARN] Kafka check error: {e}. Retrying in 5 s…")
        time.sleep(5)

    print(f"[WARN] Topic '{topic}' not found after {timeout} s — starting stream anyway "
          f"(Spark will retry internally).")

wait_for_topic(KAFKA_BOOTSTRAP, TOPIC)

# ── Spark Session ──────────────────────────────────────────────────────────────

spark = SparkSession.builder \
    .appName("ListingCleaner") \
    .config("spark.driver.memory", "1g") \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")

# Schema for the JSON data sent from the scraper
schema_str = "brand STRING, title STRING, price DOUBLE, currency STRING, url STRING, image_url STRING, sizes ARRAY<STRING>, colors ARRAY<STRING>, category STRING, available BOOLEAN, country STRING, gender STRING, description STRING"

raw_df = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP) \
    .option("subscribe", TOPIC) \
    .option("startingOffsets", "earliest") \
    .option("failOnDataLoss", "false") \
    .load()

# 1. Parse JSON
parsed = raw_df.selectExpr("CAST(value AS STRING) as json") \
    .select(from_json(col("json"), schema_str).alias("data")) \
    .select("data.*")

# 2. Anomaly Rule: Drop missing URLs or empty descriptions
clean1 = parsed.filter(col("url").isNotNull() & (col("url") != ""))
clean2 = clean1.filter(col("description").isNotNull() & (col("description") != ""))

# 3. Anomaly Rule: Drop exact duplicates (all fields match)
clean3 = clean2.dropDuplicates(["brand", "title", "price", "currency", "url", "image_url", "category", "country", "gender"])

# 4. Anomaly Rule: Drop prices <= 0
clean4 = clean3.filter(col("price") > 0)

# 5. Gender normalization: every row must end up as one of
#    {'male','female','unisex'}. Anything outside that set collapses to 'unisex'.
gender_norm = trim(lower(col("gender")))
clean5 = clean4.withColumn(
    "gender",
    when(gender_norm.isin("male", "men", "mens", "m", "man"), lit("male"))
    .when(gender_norm.isin("female", "women", "womens", "f", "woman", "ladies"), lit("female"))
    .when(gender_norm == "unisex", lit("unisex"))
    .otherwise(lit("unisex"))
)

def process_batch(df, epoch_id):
    if df.isEmpty():
        return
    
    # Calculate median per brand within this batch
    medians = df.groupBy("brand").agg(percentile_approx("price", 0.5).alias("median_price"))
    
    # Join back and filter out >= 10x median
    df_joined = df.join(medians, on="brand", how="left")
    df_filtered = df_joined.filter(
        (col("median_price").isNull()) | 
        (col("price") < col("median_price") * 10)
    ).drop("median_price")
    
    pd_df = df_filtered.toPandas()
    if not pd_df.empty:
        import json
        for col_name in ['sizes', 'colors']:
            if col_name in pd_df.columns:
                pd_df[col_name] = pd_df[col_name].apply(lambda x: json.dumps(list(x)) if hasattr(x, '__iter__') and not isinstance(x, str) else json.dumps([]) if pd.isna(x) else json.dumps([x] if isinstance(x, str) else []))
                
        # Filter down to only columns that belong in the DB
        cols_to_write = ["brand", "title", "price", "currency", "url", "image_url", "sizes", "colors", "category", "available", "country", "gender"]
        pd_df_db = pd_df[[c for c in cols_to_write if c in pd_df.columns]]
        
        conn = sqlite3.connect(DB_PATH)
        
        cols = pd_df_db.columns.tolist()
        placeholders = ','.join(['?'] * len(cols))
        col_names = ','.join(cols)
        
        sql = f"INSERT OR IGNORE INTO cleaned_listings ({col_names}) VALUES ({placeholders})"
        conn.executemany(sql, pd_df_db.values.tolist())
        conn.commit()
        
        conn.close()
        print(f"Batch {epoch_id}: wrote {len(pd_df)} cleaned records to SQLite.")

query = clean5.writeStream \
    .foreachBatch(process_batch) \
    .outputMode("update") \
    .start()

query.awaitTermination()

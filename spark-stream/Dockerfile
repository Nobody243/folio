FROM apache/spark:3.5.4-python3
USER root
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r /app/requirements.txt
COPY stream_job.py /app/
WORKDIR /app
CMD ["spark-submit", "--packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.4", "stream_job.py"]

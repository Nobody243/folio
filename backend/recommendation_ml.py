import sqlite3
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from pathlib import Path

# Paths
ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "products.db"

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def get_recommendations(search_history: list[str], limit: int = 12) -> list[dict]:
    """
    Given a list of past search queries, recommend clothing items 
    using TF-IDF and Cosine Similarity.
    """
    if not search_history:
        return []

    conn = get_conn()
    try:
        # Load all available products
        query = "SELECT * FROM cleaned_listings WHERE available = 1"
        df = pd.read_sql_query(query, conn)
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()

    if df.empty:
        return []

    # Combine text fields for the item's document
    df['text'] = df['title'].fillna('') + ' ' + df['category'].fillna('') + ' ' + df['brand'].fillna('')
    
    # Create the user profile by joining their search history into a single string
    user_profile_text = " ".join(search_history)

    # Initialize TF-IDF Vectorizer
    vectorizer = TfidfVectorizer(stop_words='english', lowercase=True)
    
    # Fit and transform the items
    tfidf_matrix = vectorizer.fit_transform(df['text'].tolist())
    
    # Transform the user profile using the same vocabulary
    user_vector = vectorizer.transform([user_profile_text])
    
    # Compute cosine similarity between user profile and all items
    cosine_similarities = cosine_similarity(user_vector, tfidf_matrix).flatten()
    
    # Get the indices of the top matches
    top_indices = cosine_similarities.argsort()[::-1][:limit]
    
    # Filter out items with 0 similarity
    recommendations = []
    for idx in top_indices:
        if cosine_similarities[idx] > 0:
            row = df.iloc[idx].to_dict()
            recommendations.append(row)
            
    if len(recommendations) < limit:
        needed = limit - len(recommendations)
        already_recommended = [r['id'] for r in recommendations]
        remaining_df = df[~df['id'].isin(already_recommended)]
        if not remaining_df.empty:
            random_pad = remaining_df.sample(n=min(needed, len(remaining_df))).to_dict('records')
            recommendations.extend(random_pad)
            
    return recommendations

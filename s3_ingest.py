import os
import boto3
import requests
import psycopg2
import hashlib
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

print("[System] Loading Native Nomic Embedder...")
embedder = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)

# --- CONFIGURATION ---
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'vibe_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port': os.getenv('DB_PORT', '5432')
}
OLLAMA_URL = "http://localhost:11434/api/embeddings" # Updated to internal docker network
AWS_REGION = "ap-southeast-1"
S3_BUCKET = "jejak-mappro-demo"
PDF_FOLDER = "3W-data/pdf-ai-train/"

# --- ADD THIS LINE ---
CURRENT_EMBEDDING_MODEL = "nomic-embed-text"

def init_db():
    print("[System] Checking PostgreSQL vector database...")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # Upgraded table with file_hash for tracking updates and vendor metadata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telecom_knowledge_base (
            id SERIAL PRIMARY KEY,
            document_name VARCHAR(255),
            file_hash VARCHAR(255),
            embedding_model VARCHAR(100) DEFAULT 'nomic-embed-text', -- <-- ADD THIS LINE
            vendor VARCHAR(50),
            chunk_text TEXT,
            embedding vector(768)
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

def delete_pdf_from_brain(document_name: str, conn=None):
    """Deletes all vector chunks associated with a specific PDF."""
    close_conn = False
    if not conn:
        conn = psycopg2.connect(**DB_CONFIG)
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM telecom_knowledge_base WHERE document_name = %s", (document_name,))
        chunk_count = cursor.fetchone()[0]

        if chunk_count > 0:
            cursor.execute("DELETE FROM telecom_knowledge_base WHERE document_name = %s", (document_name,))
            conn.commit()
            print(f"✅ Deleted old version of '{document_name}' ({chunk_count} chunks removed).")
        return True
    except Exception as e:
        print(f"❌ Error deleting document: {e}")
        return False
    finally:
        if 'cursor' in locals(): cursor.close()
        if close_conn: conn.close()

def get_embedding(text):
    try:
        # Generate the embedding directly (no web requests needed)
        # .tolist() converts the numpy array back to a standard Python list
        vector = embedder.encode(text)
        return vector.tolist()
    except Exception as e:
        print(f"[Error] Native embedding failed: {e}")
        return None

def determine_vendor(filename):
    """Simple tagging logic to improve RAG filtering later."""
    filename_lower = filename.lower()
    if 'ericsson' in filename_lower: return 'Ericsson'
    if 'zte' in filename_lower: return 'ZTE'
    if 'huawei' in filename_lower: return 'Huawei'
    return 'General'

def process_s3_pdfs():
    s3_client = boto3.client('s3', region_name=AWS_REGION)

    print(f"[AWS S3] Scanning s3://{S3_BUCKET}/{PDF_FOLDER} for manuals...")
    response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=PDF_FOLDER)

    if 'Contents' not in response:
        print("[AWS S3] Folder is empty.")
        return

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Smart Chunking: Preserves paragraphs and sentences better than blind character limits
    text_splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", "(?<=\. )", " ", ""],
        chunk_size=800,
        chunk_overlap=150,
        length_function=len
    )

    for obj in response['Contents']:
        s3_key = obj['Key']
        if not s3_key.lower().endswith('.pdf'):
            continue

        doc_name = os.path.basename(s3_key)
        # ETag is an MD5 hash of the file provided by AWS S3
        s3_etag = obj['ETag'].replace('"', '')
        local_temp_path = f"/tmp/{doc_name}"
        vendor = determine_vendor(doc_name)

        # Check if file exists, if the hash matches, AND if the model matches
        cursor.execute("SELECT file_hash, embedding_model FROM telecom_knowledge_base WHERE document_name = %s LIMIT 1", (doc_name,))
        result = cursor.fetchone()

        if result:
            existing_hash = result[0]
            existing_model = result[1]

            # If BOTH the file and the model are the same, we can safely skip
            if existing_hash == s3_etag and existing_model == CURRENT_EMBEDDING_MODEL:
                print(f"[Skip] '{doc_name}' is up to date (Hash & Model Match).")
                continue
            else:
                # If EITHER the file changed OR the model changed, wipe and redo
                print(f"[Update] '{doc_name}' or AI Model has changed! Wiping old chunks...")
                delete_pdf_from_brain(doc_name, conn)

        print(f"\n[AWS S3] Downloading file: {doc_name}...")
        s3_client.download_file(S3_BUCKET, s3_key, local_temp_path)

        print(f"[Processing] Chunking {doc_name} (Vendor: {vendor})...")
        loader = PyPDFLoader(local_temp_path)
        chunks = text_splitter.split_documents(loader.load())

        print(f"[Processing] Generated {len(chunks)} chunks. Vectorizing...")

        for i, chunk in enumerate(chunks):
            vector = get_embedding(chunk.page_content)
            if vector:
                cursor.execute("""
                    INSERT INTO telecom_knowledge_base (document_name, file_hash, embedding_model, vendor, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                """, (doc_name, s3_etag, CURRENT_EMBEDDING_MODEL, vendor, chunk.page_content, f"[{','.join(map(str, vector))}]"))

            if (i + 1) % 25 == 0:
                print(f"   -> Embedded {i + 1}/{len(chunks)} chunks...")
                conn.commit()

        conn.commit()
        os.remove(local_temp_path)
        print(f"✅ Successfully processed and stored {doc_name}!")

    cursor.close()
    conn.close()
    print("\n🎉 S3 Sync Complete!")

if __name__ == "__main__":
    init_db()
    process_s3_pdfs()

import os
import psycopg2
from langchain_core.tools import tool

_reranker = None
_embedder = None

try:
    from sentence_transformers import CrossEncoder, SentenceTransformer
    print("[System] Loading Cross-Encoder Reranker Model...")
    try:
        _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        print("[System] Loading Native Nomic Embedder...")
        _embedder = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)
    except Exception as e:
        print(f"[Warning] Could not load models: {e}")
except ImportError:
    print("[System] sentence-transformers not installed; reranker/embedder disabled.")


@tool
def search_telecom_manuals(query: str, vendor: str = "All") -> str:
    """
    Use this tool WHENEVER the user asks for theoretical definitions, general telecom concepts,
    how things work, or specific terms from textbooks/manuals (e.g., 'what is congestion',
    'define X', 'how does Y work').
    Do NOT rely solely on your internal glossary for general definitions—search the manuals first!
    Optionally pass the vendor (e.g., 'Ericsson', 'ZTE') if mentioned in the prompt.
    """
    print(f"[Agent Tool] Searching manuals for: {query} | Vendor: {vendor}")
    if _embedder is None:
        return "Manual search unavailable: embedding model not loaded (sentence-transformers not installed)."

    try:
        query_vector = _embedder.encode(query).tolist()
        vector_str   = f"[{','.join(map(str, query_vector))}]"

        conn = psycopg2.connect(
            host=os.getenv('DB_HOST',     'vibe_db'),
            database=os.getenv('DB_NAME', 'vibe_db'),
            user=os.getenv('DB_USER',     'postgres'),
            password=os.getenv('DB_PASSWORD'),
            port=os.getenv('DB_PORT',     '5432'),
        )
        cursor = conn.cursor()

        params = [vector_str, f"%{query}%"]
        vendor_filter = ""
        if vendor and vendor != "All":
            vendor_filter = "WHERE vendor ILIKE %s"
            params.append(f"%{vendor}%")

        sql = f"""
            SELECT document_name, chunk_text
            FROM telecom_knowledge_base
            {vendor_filter}
            ORDER BY
                (embedding <=> %s::vector) * 0.7 +
                (CASE WHEN chunk_text ILIKE %s THEN 0 ELSE 0.3 END) ASC
            LIMIT 10;
        """
        cursor.execute(sql, tuple(params))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        if not results:
            return "I searched the engineering manuals but couldn't find any relevant information."

        if _reranker and len(results) > 1:
            print("[Agent Tool] Reranking the top 10 search results...")
            pairs  = [[query, text] for doc, text in results]
            scores = _reranker.predict(pairs)
            scored = sorted(zip(scores, results), key=lambda x: x[0], reverse=True)
            final_results = [res for _, res in scored[:3]]
        else:
            final_results = results[:3]

        formatted = "Here is the exact, reranked information from the engineering manuals:\n\n"
        for doc, text in final_results:
            formatted += f"--- Source: {doc} ---\n{text}\n\n"

        return formatted

    except Exception as e:
        return f"Error searching the knowledge base: {str(e)}"

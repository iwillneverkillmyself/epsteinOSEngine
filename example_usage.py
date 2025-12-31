"""Example usage of the OCR RAG system."""
import asyncio
import httpx


async def example_searches():
    """Example API usage."""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient() as client:
        # 1. Health check
        print("1. Health Check")
        response = await client.get(f"{base_url}/health")
        print(f"   Status: {response.json()}\n")
        
        # 2. Get stats
        print("2. System Stats")
        response = await client.get(f"{base_url}/stats")
        stats = response.json()
        print(f"   Documents: {stats['documents']}")
        print(f"   Pages: {stats['image_pages']}")
        print(f"   OCR Texts: {stats['ocr_texts']}")
        print(f"   Entities: {stats['entities']}\n")
        
        # 3. Keyword search
        print("3. Keyword Search")
        response = await client.post(
            f"{base_url}/search",
            json={
                "query": "example",
                "search_type": "keyword",
                "limit": 10
            }
        )
        results = response.json()
        print(f"   Found {results['count']} results")
        if results['results']:
            print(f"   First result snippet: {results['results'][0]['snippet'][:100]}...\n")
        
        # 4. Entity search (names)
        print("4. Entity Search (Names)")
        response = await client.get(
            f"{base_url}/search/entity",
            params={
                "entity_type": "name",
                "entity_value": "John",
                "limit": 10
            }
        )
        results = response.json()
        print(f"   Found {results['count']} name matches\n")
        
        # 5. Phrase search
        print("5. Phrase Search")
        response = await client.get(
            f"{base_url}/search",
            params={
                "q": "exact phrase",
                "search_type": "phrase",
                "limit": 10
            }
        )
        results = response.json()
        print(f"   Found {results['count']} phrase matches\n")


if __name__ == "__main__":
    print("OCR RAG System - Example Usage")
    print("=" * 50)
    print("\nMake sure the API server is running:")
    print("  python main.py")
    print("\nThen run this script to see example searches.\n")
    
    try:
        asyncio.run(example_searches())
    except httpx.ConnectError:
        print("Error: Could not connect to API server.")
        print("Please start the server first: python main.py")
    except Exception as e:
        print(f"Error: {e}")





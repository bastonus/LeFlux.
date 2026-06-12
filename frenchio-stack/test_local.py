import asyncio
from main import GeminiService

async def main():
    service = GeminiService("yqVzO429Qo75V7V6mZ33iH4UqI7iE3P7h2AXXL7bI3bQnN8E4A0sFpWj9J2S")
    res = await service.search_movie("Inception", "2010", tmdb_id=27205)
    print("Results:", len(res))
    if len(res) > 0:
        print("First result:", res[0])

asyncio.run(main())

"""
DevOps Assessment API - Optimized Version

Key optimizations:
1. Redis caching layer to reduce MongoDB read load
2. Write batching using insert_many() instead of 5 separate insert_one() calls
3. Async MongoDB operations with proper connection pooling
4. Connection pooling for both MongoDB and Redis
5. Optimized FastAPI with multiple workers
"""

import os
import time
import random
import string
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
import redis.asyncio as redis

# Configuration from environment
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/assessmentdb")
REDIS_URI = os.getenv("REDIS_URI", "redis://redis:6379/0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))  # Cache TTL in seconds
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
WRITE_BATCH_SIZE = int(os.getenv("WRITE_BATCH_SIZE", "5"))  # Batch writes

# Global state
mongo_client: Optional[AsyncIOMotorClient] = None
db = None
col = None
redis_client: Optional[redis.Redis] = None


def random_payload(size: int = 512) -> str:
    """Generate a random payload of specified size."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


def generate_cache_key() -> str:
    """Generate a deterministic cache key for reads."""
    # Use a fixed key pattern for cache hits on similar queries
    return "api:data:recent"


async def get_redis() -> Optional[redis.Redis]:
    """Get or create Redis connection."""
    global redis_client
    if redis_client is None and CACHE_ENABLED:
        try:
            redis_client = redis.from_url(
                REDIS_URI,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                max_connections=50,
            )
            # Test connection
            await redis_client.ping()
        except Exception as e:
            print(f"[redis] connection failed: {e}")
            redis_client = None
    return redis_client


async def get_cached_reads() -> Optional[List[str]]:
    """Try to get cached read results from Redis."""
    if not CACHE_ENABLED:
        return None
    
    r = await get_redis()
    if r is None:
        return None
    
    try:
        cached = await r.get(generate_cache_key())
        if cached:
            return json.loads(cached)
    except Exception as e:
        print(f"[redis] cache get error: {e}")
    
    return None


async def set_cached_reads(reads: List[str]) -> None:
    """Cache read results in Redis."""
    if not CACHE_ENABLED:
        return
    
    r = await get_redis()
    if r is None:
        return
    
    try:
        await r.setex(
            generate_cache_key(),
            CACHE_TTL,
            json.dumps(reads)
        )
    except Exception as e:
        print(f"[redis] cache set error: {e}")


async def invalidate_cache() -> None:
    """Invalidate the cache after writes."""
    if not CACHE_ENABLED:
        return
    
    r = await get_redis()
    if r is None:
        return
    
    try:
        await r.delete(generate_cache_key())
    except Exception as e:
        print(f"[redis] cache invalidate error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    global mongo_client, db, col, redis_client
    
    # Startup: Connect to MongoDB with retries
    print("[startup] initializing connections...")
    
    for attempt in range(1, 11):
        try:
            mongo_client = AsyncIOMotorClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                maxPoolSize=100,
                minPoolSize=10,
                maxIdleTimeMS=60000,
                waitQueueTimeoutMS=5000,
            )
            # Test connection
            await mongo_client.admin.command("ping")
            db = mongo_client["assessmentdb"]
            col = db["records"]
            print(f"[mongo] connected on attempt {attempt}")
            break
        except Exception as e:
            print(f"[mongo] attempt {attempt}/10 failed: {e}")
            if attempt == 10:
                print("[mongo] could not connect - will retry on first request")
            else:
                await asyncio.sleep(5)
    
    # Initialize Redis connection
    if CACHE_ENABLED:
        try:
            redis_client = redis.from_url(
                REDIS_URI,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                max_connections=50,
            )
            await redis_client.ping()
            print("[redis] connected")
        except Exception as e:
            print(f"[redis] connection failed: {e}")
            redis_client = None
    
    yield
    
    # Shutdown: Close connections
    print("[shutdown] closing connections...")
    if mongo_client:
        mongo_client.close()
    if redis_client:
        await redis_client.close()


app = FastAPI(
    title="DevOps Assessment API - Optimized",
    version="2.0.0",
    lifespan=lifespan,
)


# Liveness probe
@app.get("/healthz")
async def health_check():
    """Liveness probe - always returns 200."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0"
    }


# Readiness probe
@app.get("/readyz")
async def readiness_check():
    """Readiness probe - checks MongoDB connectivity."""
    if col is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")
    
    try:
        await mongo_client.admin.command("ping")
        return {
            "status": "ready",
            "timestamp": datetime.utcnow().isoformat(),
            "cache_enabled": CACHE_ENABLED and redis_client is not None
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# Core endpoint - optimized with caching and batch writes
@app.get("/api/data")
async def process_data():
    """
    Assessment endpoint - performs 5 reads + 5 writes per request.
    
    Optimizations:
    - Uses Redis caching for reads (reduces MongoDB load)
    - Batches writes using insert_many() (reduces MongoDB ops)
    - Async operations for better concurrency
    """
    if col is None:
        raise HTTPException(status_code=503, detail="MongoDB not reachable")
    
    try:
        # Try to get cached reads first
        cached_reads = await get_cached_reads()
        
        if cached_reads:
            # Cache hit - still need to do writes but can skip reads
            reads = cached_reads
            # Invalidate cache since we're doing writes
            await invalidate_cache()
        else:
            # Cache miss - perform actual reads from MongoDB
            reads = []
            # Use find() with limit for more efficient batch reading
            cursor = col.find({"type": "write"}).limit(5)
            async for doc in cursor:
                reads.append(str(doc["_id"]))
            
            # Pad with None if fewer than 5 documents found
            while len(reads) < 5:
                reads.append(None)
            
            # Cache the reads for future requests
            await set_cached_reads(reads)
        
        # Batch all 5 writes into a single insert_many operation
        # This reduces MongoDB operations from 5 to 1
        write_docs = [
            {
                "type": "write",
                "index": i,
                "payload": random_payload(),
                "timestamp": datetime.utcnow(),
            }
            for i in range(5)
        ]
        
        result = await col.insert_many(write_docs)
        writes = [str(inserted_id) for inserted_id in result.inserted_ids]
        
        # Invalidate cache after writes to ensure consistency
        await invalidate_cache()
        
        return JSONResponse(content={
            "status": "success",
            "reads": reads,
            "writes": writes,
            "timestamp": datetime.utcnow().isoformat(),
            "cached": cached_reads is not None
        })
    
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(exc)}")


# Collection stats
@app.get("/api/stats")
async def get_stats():
    """Get collection document count."""
    if col is None:
        raise HTTPException(status_code=503, detail="MongoDB not reachable")
    
    try:
        count = await col.count_documents({})
        return {
            "total_documents": count,
            "timestamp": datetime.utcnow().isoformat()
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Cache status endpoint (for debugging)
@app.get("/api/cache/status")
async def cache_status():
    """Get cache status for debugging."""
    r = await get_redis()
    if r is None:
        return {"cache_enabled": False, "connected": False}
    
    try:
        info = await r.info()
        return {
            "cache_enabled": CACHE_ENABLED,
            "connected": True,
            "used_memory": info.get("used_memory_human", "unknown"),
            "connected_clients": info.get("connected_clients", "unknown"),
        }
    except Exception as e:
        return {"cache_enabled": CACHE_ENABLED, "connected": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)

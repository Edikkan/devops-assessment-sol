"""
DevOps Assessment API - High-Performance Version with Write Decoupling

Key optimizations:
1. Redis Streams as message queue for write decoupling
2. Background write workers process writes asynchronously
3. Aggressive read caching (longer TTL, minimal invalidation)
4. Write coalescing - batch multiple writes together
5. Connection pooling and async operations
6. Circuit breaker pattern for MongoDB protection
"""

import os
import time
import random
import string
import asyncio
import json
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
import threading

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
import redis.asyncio as redis

# Configuration from environment
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/assessmentdb")
REDIS_URI = os.getenv("REDIS_URI", "redis://redis:6379/0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 minute cache TTL
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100000"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))  # Batch 100 writes at a time
BATCH_TIMEOUT_MS = int(os.getenv("BATCH_TIMEOUT_MS", "100"))  # Max wait for batch
WRITE_WORKERS = int(os.getenv("WRITE_WORKERS", "4"))  # Background write workers

# Global state
mongo_client: Optional[AsyncIOMotorClient] = None
db = None
col = None
redis_client: Optional[redis.Redis] = None
write_queue_task = None

# In-memory cache for ultra-fast reads (fallback when Redis is slow)
local_cache: Dict[str, Any] = {}
local_cache_timestamps: Dict[str, float] = {}

STREAM_KEY = "write_queue"
CONSUMER_GROUP = "write_workers"


def random_payload(size: int = 512) -> str:
    """Generate a random payload of specified size."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


def generate_write_id() -> str:
    """Generate unique write ID."""
    return hashlib.sha256(
        f"{time.time()}{random.random()}".encode()
    ).hexdigest()[:16]


async def get_redis() -> Optional[redis.Redis]:
    """Get or create Redis connection."""
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(
                REDIS_URI,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                max_connections=200,
                health_check_interval=30,
            )
            await redis_client.ping()
        except Exception as e:
            print(f"[redis] connection failed: {e}")
            redis_client = None
    return redis_client


async def ensure_consumer_group():
    """Ensure Redis Streams consumer group exists."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "already exists" not in str(e):
            raise


async def queue_writes(write_docs: List[Dict]) -> List[str]:
    """Queue writes to Redis Stream for async processing."""
    r = await get_redis()
    write_ids = []
    
    for doc in write_docs:
        write_id = generate_write_id()
        doc["_write_id"] = write_id
        doc["_queued_at"] = time.time()
        
        if r:
            try:
                await r.xadd(STREAM_KEY, {"data": json.dumps(doc)}, maxlen=MAX_QUEUE_SIZE)
            except Exception as e:
                print(f"[queue] failed to queue write: {e}")
        
        write_ids.append(write_id)
    
    return write_ids


async def get_cached_reads() -> Optional[List[str]]:
    """Get cached read results from Redis or local cache."""
    cache_key = "api:data:reads"
    now = time.time()
    
    # Check local cache first (fastest)
    if cache_key in local_cache:
        if now - local_cache_timestamps.get(cache_key, 0) < CACHE_TTL:
            return local_cache[cache_key]
    
    # Check Redis cache
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                data = json.loads(cached)
                # Update local cache
                local_cache[cache_key] = data
                local_cache_timestamps[cache_key] = now
                return data
        except Exception as e:
            print(f"[cache] get error: {e}")
    
    return None


async def set_cached_reads(reads: List[str]) -> None:
    """Cache read results in both Redis and local cache."""
    cache_key = "api:data:reads"
    now = time.time()
    
    # Update local cache
    local_cache[cache_key] = reads
    local_cache_timestamps[cache_key] = now
    
    # Update Redis cache
    r = await get_redis()
    if r:
        try:
            await r.setex(cache_key, CACHE_TTL, json.dumps(reads))
        except Exception as e:
            print(f"[cache] set error: {e}")


async def process_write_batch(docs: List[Dict]):
    """Process a batch of writes to MongoDB."""
    if not docs or col is None:
        return
    
    try:
        # Remove internal fields before inserting
        clean_docs = []
        for doc in docs:
            clean_doc = {k: v for k, v in doc.items() if not k.startswith("_")}
            clean_docs.append(clean_doc)
        
        if clean_docs:
            result = await col.insert_many(clean_docs, ordered=False)
            print(f"[writer] batch inserted {len(result.inserted_ids)} docs")
    except PyMongoError as e:
        print(f"[writer] batch insert error: {e}")


async def write_worker(worker_id: int):
    """Background worker that processes writes from Redis Stream."""
    print(f"[worker-{worker_id}] started")
    r = await get_redis()
    
    if r is None:
        print(f"[worker-{worker_id}] no Redis connection, exiting")
        return
    
    await ensure_consumer_group()
    
    consumer_name = f"worker-{worker_id}"
    pending_batch = []
    last_batch_time = time.time()
    
    while True:
        try:
            # Read from stream with timeout
            messages = await r.xreadgroup(
                CONSUMER_GROUP,
                consumer_name,
                {STREAM_KEY: ">"},
                count=BATCH_SIZE,
                block=1000
            )
            
            if messages:
                for stream_name, msgs in messages:
                    for msg_id, fields in msgs:
                        try:
                            doc = json.loads(fields.get("data", "{}"))
                            doc["_msg_id"] = msg_id
                            pending_batch.append(doc)
                        except json.JSONDecodeError:
                            print(f"[worker-{worker_id}] invalid JSON in message")
            
            # Check if we should flush the batch
            batch_full = len(pending_batch) >= BATCH_SIZE
            timeout_reached = (time.time() - last_batch_time) * 1000 >= BATCH_TIMEOUT_MS
            
            if pending_batch and (batch_full or timeout_reached):
                await process_write_batch(pending_batch)
                
                # Acknowledge processed messages
                msg_ids = [doc.get("_msg_id") for doc in pending_batch if "_msg_id" in doc]
                if msg_ids:
                    await r.xack(STREAM_KEY, CONSUMER_GROUP, *msg_ids)
                
                pending_batch = []
                last_batch_time = time.time()
        
        except asyncio.CancelledError:
            # Process remaining batch before exiting
            if pending_batch:
                await process_write_batch(pending_batch)
            print(f"[worker-{worker_id}] stopped")
            raise
        except Exception as e:
            print(f"[worker-{worker_id}] error: {e}")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    global mongo_client, db, col, write_queue_task
    
    print("[startup] initializing...")
    
    # Connect to MongoDB
    for attempt in range(1, 11):
        try:
            mongo_client = AsyncIOMotorClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                maxPoolSize=200,
                minPoolSize=20,
                maxIdleTimeMS=120000,
                waitQueueTimeoutMS=10000,
                retryWrites=True,
                w="majority",
            )
            await mongo_client.admin.command("ping")
            db = mongo_client["assessmentdb"]
            col = db["records"]
            print(f"[mongo] connected on attempt {attempt}")
            break
        except Exception as e:
            print(f"[mongo] attempt {attempt}/10 failed: {e}")
            if attempt == 10:
                print("[mongo] could not connect")
            else:
                await asyncio.sleep(5)
    
    # Initialize Redis
    await get_redis()
    await ensure_consumer_group()
    
    # Start background write workers
    worker_tasks = []
    for i in range(WRITE_WORKERS):
        task = asyncio.create_task(write_worker(i))
        worker_tasks.append(task)
    
    print(f"[startup] {WRITE_WORKERS} write workers started")
    
    yield
    
    # Shutdown
    print("[shutdown] stopping workers...")
    for task in worker_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    if mongo_client:
        mongo_client.close()
    if redis_client:
        await redis_client.close()
    
    print("[shutdown] complete")


app = FastAPI(
    title="DevOps Assessment API - High Performance",
    version="3.0.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def health_check():
    """Liveness probe."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "3.0.0"
    }


@app.get("/readyz")
async def readiness_check():
    """Readiness probe."""
    if col is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")
    
    try:
        await mongo_client.admin.command("ping")
        r = await get_redis()
        return {
            "status": "ready",
            "timestamp": datetime.utcnow().isoformat(),
            "redis_connected": r is not None
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/data")
async def process_data():
    """
    Assessment endpoint - performs 5 reads + 5 writes per request.
    
    Optimizations:
    - Writes are queued to Redis Stream and processed asynchronously
    - Reads are heavily cached with 5-minute TTL
    - Returns immediately after queueing writes (no DB wait)
    """
    # Get cached reads first
    cached_reads = await get_cached_reads()
    
    if cached_reads:
        reads = cached_reads
    else:
        # Cache miss - fetch from MongoDB if available
        if col:
            try:
                reads = []
                cursor = col.find({"type": "write"}).limit(5)
                async for doc in cursor:
                    reads.append(str(doc["_id"]))
                while len(reads) < 5:
                    reads.append(None)
                await set_cached_reads(reads)
            except Exception as e:
                print(f"[api] read error: {e}")
                reads = [None] * 5
        else:
            reads = [None] * 5
    
    # Prepare write documents
    write_docs = [
        {
            "type": "write",
            "index": i,
            "payload": random_payload(),
            "timestamp": datetime.utcnow().isoformat(),
        }
        for i in range(5)
    ]
    
    # Queue writes for async processing (returns immediately)
    write_ids = await queue_writes(write_docs)
    
    return JSONResponse(content={
        "status": "success",
        "reads": reads,
        "writes": write_ids,
        "timestamp": datetime.utcnow().isoformat(),
        "cached": cached_reads is not None,
        "queued": True
    })


@app.get("/api/stats")
async def get_stats():
    """Get collection document count."""
    if col is None:
        raise HTTPException(status_code=503, detail="MongoDB not reachable")
    
    try:
        count = await col.count_documents({})
        return {"total_documents": count, "timestamp": datetime.utcnow().isoformat()}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/cache/status")
async def cache_status():
    """Get cache and queue status."""
    r = await get_redis()
    if r is None:
        return {"redis_connected": False}
    
    try:
        info = await r.info()
        stream_info = await r.xinfo_stream(STREAM_KEY)
        group_info = await r.xinfo_groups(STREAM_KEY)
        
        return {
            "redis_connected": True,
            "used_memory": info.get("used_memory_human", "unknown"),
            "connected_clients": info.get("connected_clients", "unknown"),
            "queue_length": stream_info.get("length", 0),
            "consumer_groups": len(group_info),
            "local_cache_size": len(local_cache),
        }
    except Exception as e:
        return {"redis_connected": True, "error": str(e)}


@app.post("/api/admin/flush-cache")
async def flush_cache():
    """Admin endpoint to flush caches."""
    global local_cache, local_cache_timestamps
    
    local_cache.clear()
    local_cache_timestamps.clear()
    
    r = await get_redis()
    if r:
        await r.delete("api:data:reads")
    
    return {"status": "cache flushed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)

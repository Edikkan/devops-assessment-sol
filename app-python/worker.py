#!/usr/bin/env python3
"""
Background worker for async MongoDB writes.
Consumes from Redis queue and batches inserts to MongoDB.
"""

import os
import time
import json
import signal
import sys
from datetime import datetime

import redis
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/assessmentdb")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_LIST = "write_queue"
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))  # Documents per batch
SLEEP_SECS = float(os.getenv("SLEEP_SECS", "0.5"))  # Sleep between batches

# Global flag for graceful shutdown
running = True

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global running
    print("\n[worker] Shutdown signal received, finishing current batch...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def connect_mongo():
    """Establish MongoDB connection."""
    try:
        client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000
        )
        client.admin.command('ping')
        db = client["assessmentdb"]
        col = db["records"]
        print(f"[worker] Connected to MongoDB")
        return client, col
    except PyMongoError as e:
        print(f"[worker] MongoDB connection failed: {e}")
        return None, None

def connect_redis():
    """Establish Redis connection."""
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            socket_connect_timeout=5
        )
        r.ping()
        print(f"[worker] Connected to Redis")
        return r
    except redis.ConnectionError as e:
        print(f"[worker] Redis connection failed: {e}")
        return None

def main():
    """Main worker loop."""
    print("[worker] Starting background worker for MongoDB async writes")
    print(f"[worker] BATCH_SIZE={BATCH_SIZE}, SLEEP_SECS={SLEEP_SECS}")
    
    # Connect to Redis
    redis_client = connect_redis()
    if not redis_client:
        print("[worker] Failed to connect to Redis, retrying in 5s...")
        time.sleep(5)
        return
    
    # Connect to MongoDB
    mongo_client, mongo_col = connect_mongo()
    if not mongo_col:
        print("[worker] Failed to connect to MongoDB, retrying in 5s...")
        time.sleep(5)
        return
    
    stats = {"processed": 0, "batches": 0, "errors": 0}
    
    while running:
        try:
            # Pop batch from Redis queue
            pipeline = redis_client.pipeline()
            pipeline.lrange(QUEUE_LIST, 0, BATCH_SIZE - 1)
            pipeline.ltrim(QUEUE_LIST, BATCH_SIZE, -1)
            results = pipeline.execute()
            
            items = results[0]  # List of JSON strings
            
            if not items:
                time.sleep(0.1)
                continue
            
            # Parse and insert documents
            docs = [json.loads(item) for item in items]
            
            if docs:
                # Add processing timestamp
                for doc in docs:
                    doc["processed_at"] = datetime.utcnow().isoformat()
                
                # Bulk insert
                result = mongo_col.insert_many(docs, ordered=False)
                stats["processed"] += len(result.inserted_ids)
                stats["batches"] += 1
                print(f"[worker] Inserted {len(result.inserted_ids)} documents")
            
            # Rate limiting
            time.sleep(SLEEP_SECS)
            
        except redis.RedisError as e:
            print(f"[worker] Redis error: {e}")
            stats["errors"] += 1
            time.sleep(2)
        except PyMongoError as e:
            print(f"[worker] MongoDB error: {e}")
            stats["errors"] += 1
            time.sleep(2)
        except Exception as e:
            print(f"[worker] Unexpected error: {e}")
            stats["errors"] += 1
            time.sleep(1)
    
    print(f"[worker] Shutting down. Processed: {stats['processed']}")
    mongo_client.close()
    redis_client.close()

if __name__ == "__main__":
    while running:
        try:
            main()
        except Exception as e:
            print(f"[worker] Fatal error, restarting: {e}")
            time.sleep(5)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import os
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(
    title="MCQ Test Platform API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Configuration
MONGO_URL = os.getenv("Mongo_URL")
admin_email_ = os.getenv("ADMIN_EMAIL")
pass_email = os.getenv("ADMIN_PASSWORD")

# Validate environment variables
if not MONGO_URL:
    logger.error("MONGO_URL environment variable is not set!")
    raise ValueError("MONGO_URL is required")

if not admin_email_:
    logger.error("ADMIN_EMAIL environment variable is not set!")
    raise ValueError("ADMIN_EMAIL is required")

if not pass_email:
    logger.error("ADMIN_PASSWORD environment variable is not set!")
    raise ValueError("ADMIN_PASSWORD is required")

logger.info("Environment variables loaded successfully")

# MongoDB Client
try:
    client = AsyncIOMotorClient(
        MONGO_URL,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=10000,
    )
    db = client["mcq_platform"]
    
    users_collection = db["users"]
    questions_collection = db["questions"]
    results_collection = db["results"]
    
    logger.info("MongoDB client initialized")
except Exception as e:
    logger.error(f"Failed to initialize MongoDB client: {str(e)}")
    raise


# ================= STARTUP =================

@app.on_event("startup")
async def startup_db_client():
    try:
        await client.admin.command('ping')
        logger.info("Successfully connected to MongoDB!")
        
        await users_collection.create_index("email", unique=True)
        await questions_collection.create_index("type")
        await results_collection.create_index("email")
        
        logger.info("Database indexes created successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {str(e)}")
        logger.error("Please check your MONGO_URL environment variable")


@app.on_event("shutdown")
async def shutdown_db_client():
    try:
        client.close()
        logger.info("MongoDB connection closed")
    except Exception as e:
        logger.error(f"Error closing MongoDB connection: {str(e)}")


# ================= HELPERS =================

def generate_password(first_name, birth_year, favorite, domain):
    return f"{first_name}{birth_year}{favorite}{domain}".replace(" ", "")


async def verify_admin(email, password):
    return email == admin_email_ and password == pass_email


# ================= USER =================

@app.post("/api/register")
async def register_user(request: Request):
    try:
        data = await request.json()

        email = data.get("email")
        if not email:
            raise HTTPException(400, "Email required")

        existing_user = await users_collection.find_one({"email": email})
        if existing_user:
            raise HTTPException(400, "Email already registered")

        birth_year = data.get("date_of_birth", "").split("-")[0] if data.get("date_of_birth") else ""

        password = generate_password(
            data.get("first_name", ""),
            birth_year,
            data.get("favorite_unique_name", ""),
            data.get("internship_domain", "")
        )

        user_doc = {
            "name": f"{data.get('first_name', '')} {data.get('last_name', '')}",
            "email": email,
            "domain": data.get("internship_domain"),
            "role": data.get("internship_role"),
            "date_of_birth": data.get("date_of_birth"),
            "password": password,
            "aptitude_score": 0,
            "technical_score": 0,
            "total_score": 0,
            "test_completed": False,
            "created_at": datetime.utcnow()
        }

        await users_collection.insert_one(user_doc)

        return {"message": "Registration successful", "email": email, "password": password}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        raise HTTPException(500, f"Registration failed: {str(e)}")


@app.post("/api/login")
async def login_user(request: Request):
    try:
        data = await request.json()

        user = await users_collection.find_one({"email": data.get("email")})

        if not user or user["password"] != data.get("password"):
            raise HTTPException(401, "Invalid credentials")

        if user.get("test_completed"):
            raise HTTPException(403, "Test already completed")

        return {
            "message": "Login successful",
            "user": {
                "name": user["name"],
                "email": user["email"],
                "domain": user["domain"],
                "role": user["role"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(500, f"Login failed: {str(e)}")


@app.post("/api/admin/login")
async def admin_login(request: Request):
    try:
        data = await request.json()

        if not await verify_admin(data.get("email"), data.get("password")):
            raise HTTPException(401, "Invalid admin credentials")

        return {"message": "Admin login successful"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin login error: {str(e)}")
        raise HTTPException(500, f"Admin login failed: {str(e)}")


# ================= QUESTIONS =================

@app.post("/api/admin/questions")
async def add_question(request: Request):
    try:
        data = await request.json()

        if data.get("type") not in ["aptitude", "technical"]:
            raise HTTPException(400, "Invalid question type. Must be 'aptitude' or 'technical'")

        question_doc = {
            "question": data.get("question"),
            "options": data.get("options"),
            "answer": data.get("answer"),
            "type": data.get("type"),
            "created_at": datetime.utcnow()
        }

        result = await questions_collection.insert_one(question_doc)

        return {"message": "Question added", "id": str(result.inserted_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add question error: {str(e)}")
        raise HTTPException(500, f"Failed to add question: {str(e)}")


@app.get("/api/admin/questions")
async def get_all_questions():
    try:
        questions = []
        async for q in questions_collection.find():
            q["_id"] = str(q["_id"])
            questions.append(q)

        return {"questions": questions}
    except Exception as e:
        logger.error(f"Get questions error: {str(e)}")
        raise HTTPException(500, f"Failed to fetch questions: {str(e)}")


@app.put("/api/admin/questions/{question_id}")
async def update_question(question_id: str, request: Request):
    try:
        from bson import ObjectId
        data = await request.json()

        if not data:
            raise HTTPException(400, "No data")

        if "type" in data and data["type"] not in ["aptitude", "technical"]:
            raise HTTPException(400, "Invalid question type. Must be 'aptitude' or 'technical'")

        result = await questions_collection.update_one(
            {"_id": ObjectId(question_id)},
            {"$set": data}
        )

        if result.matched_count == 0:
            raise HTTPException(404, "Not found")

        return {"message": "Updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update question error: {str(e)}")
        raise HTTPException(500, f"Failed to update question: {str(e)}")


@app.get("/api/admin/users")
async def get_all_users():
    try:
        users = []
        async for user in users_collection.find():
            user["_id"] = str(user["_id"])
            users.append(user)

        return {"users": users}
    except Exception as e:
        logger.error(f"Get users error: {str(e)}")
        raise HTTPException(500, f"Failed to fetch users: {str(e)}")


@app.delete("/api/admin/questions/{question_id}")
async def delete_question(question_id: str):
    try:
        from bson import ObjectId

        result = await questions_collection.delete_one({"_id": ObjectId(question_id)})

        if result.deleted_count == 0:
            raise HTTPException(404, "Not found")

        return {"message": "Deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete question error: {str(e)}")
        raise HTTPException(500, f"Failed to delete question: {str(e)}")


# ================= TEST =================

@app.get("/api/questions/{question_type}")
async def get_questions_by_type(question_type: str):
    try:
        if question_type not in ["aptitude", "technical"]:
            raise HTTPException(400, "Invalid type")

        questions = []
        async for q in questions_collection.find({"type": question_type}):
            questions.append({
                "_id": str(q["_id"]),
                "question": q["question"],
                "options": q["options"],
                "type": q["type"]
            })

        return {"questions": questions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get questions by type error: {str(e)}")
        raise HTTPException(500, f"Failed to fetch questions: {str(e)}")


@app.post("/api/submit-test")
async def submit_test(request: Request):
    try:
        data = await request.json()

        email = data.get("email")

        user = await users_collection.find_one({"email": email})
        if not user:
            raise HTTPException(404, "User not found")

        if user.get("test_completed"):
            raise HTTPException(403, "Already completed")

        aptitude_answers = data.get("aptitude_answers", [])
        technical_answers = data.get("technical_answers", [])

        aptitude_questions = [q async for q in questions_collection.find({"type": "aptitude"})]
        technical_questions = [q async for q in questions_collection.find({"type": "technical"})]

        aptitude_score = sum(
            1 for i, ans in enumerate(aptitude_answers)
            if i < len(aptitude_questions) and ans == aptitude_questions[i]["answer"]
        )

        technical_score = sum(
            1 for i, ans in enumerate(technical_answers)
            if i < len(technical_questions) and ans == technical_questions[i]["answer"]
        )

        total = aptitude_score + technical_score

        await users_collection.update_one(
            {"email": email},
            {"$set": {
                "aptitude_score": aptitude_score,
                "technical_score": technical_score,
                "total_score": total,
                "test_completed": True,
                "completed_at": datetime.utcnow()
            }}
        )

        await results_collection.insert_one({
            "email": email,
            "aptitude_score": aptitude_score,
            "technical_score": technical_score,
            "total_score": total,
            "submitted_at": datetime.utcnow()
        })

        return {
            "message": "Submitted",
            "total_score": total
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Submit test error: {str(e)}")
        raise HTTPException(500, f"Failed to submit test: {str(e)}")


# ================= ROOT =================

@app.get("/")
async def root():
    return {
        "message": "MCQ API Running",
        "status": "ok",
        "version": "1.0.0"
    }


@app.get("/health")
async def health():
    try:
        await client.admin.command('ping')
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check - DB connection failed: {str(e)}")
        db_status = "disconnected"
    
    return {
        "status": "ok",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat()
    }

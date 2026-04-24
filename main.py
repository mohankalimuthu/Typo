from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import os
from dotenv import load_dotenv

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

# MongoDB
MONGO_URL = os.getenv("MONGO_URL")
admin_email_ = os.getenv("ADMIN_EMAIL")
pass_email = os.getenv("ADMIN_PASSWORD")

client = AsyncIOMotorClient(MONGO_URL)
db = client["mcq_platform"]

users_collection = db["users"]
questions_collection = db["questions"]
results_collection = db["results"]


# ================= STARTUP =================

@app.on_event("startup")
async def startup_db_client():
    # Create indexes for better performance
    await users_collection.create_index("email", unique=True)
    await questions_collection.create_index("type")
    await results_collection.create_index("email")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


# ================= HELPERS =================

def generate_password(first_name, birth_year, favorite, domain):
    return f"{first_name}{birth_year}{favorite}{domain}".replace(" ", "")


async def verify_admin(email, password):
    return email == admin_email_ and password == pass_email


# ================= USER =================

@app.post("/api/register")
async def register_user(request: Request):
    data = await request.json()

    email = data.get("email")
    if not email:
        raise HTTPException(400, "Email required")

    existing_user = await users_collection.find_one({"email": email})
    if existing_user:
        raise HTTPException(400, "Email already registered")

    birth_year = data.get("date_of_birth", "").split("-")[0]

    password = generate_password(
        data.get("first_name"),
        birth_year,
        data.get("favorite_unique_name"),
        data.get("internship_domain")
    )

    user_doc = {
        "name": f"{data.get('first_name')} {data.get('last_name')}",
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


@app.post("/api/login")
async def login_user(request: Request):
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


@app.post("/api/admin/login")
async def admin_login(request: Request):
    data = await request.json()

    if not await verify_admin(data.get("email"), data.get("password")):
        raise HTTPException(401, "Invalid admin credentials")

    return {"message": "Admin login successful"}


# ================= QUESTIONS =================

@app.post("/api/admin/questions")
async def add_question(request: Request):
    data = await request.json()

    # Validate question type
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


@app.get("/api/admin/questions")
async def get_all_questions():
    questions = []
    async for q in questions_collection.find():
        q["_id"] = str(q["_id"])
        questions.append(q)

    return {"questions": questions}


@app.put("/api/admin/questions/{question_id}")
async def update_question(question_id: str, request: Request):
    from bson import ObjectId
    data = await request.json()

    if not data:
        raise HTTPException(400, "No data")

    # Validate question type if being updated
    if "type" in data and data["type"] not in ["aptitude", "technical"]:
        raise HTTPException(400, "Invalid question type. Must be 'aptitude' or 'technical'")

    result = await questions_collection.update_one(
        {"_id": ObjectId(question_id)},
        {"$set": data}
    )

    if result.matched_count == 0:
        raise HTTPException(404, "Not found")

    return {"message": "Updated"}


@app.get("/api/admin/users")
async def get_all_users():
    users = []
    async for user in users_collection.find():
        user["_id"] = str(user["_id"])
        users.append(user)

    return {"users": users}


@app.delete("/api/admin/questions/{question_id}")
async def delete_question(question_id: str):
    from bson import ObjectId

    result = await questions_collection.delete_one({"_id": ObjectId(question_id)})

    if result.deleted_count == 0:
        raise HTTPException(404, "Not found")

    return {"message": "Deleted"}


# ================= TEST =================

@app.get("/api/questions/{question_type}")
async def get_questions_by_type(question_type: str):
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


@app.post("/api/submit-test")
async def submit_test(request: Request):
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


# ================= ROOT =================

@app.get("/")
async def root():
    return {"message": "MCQ API Running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
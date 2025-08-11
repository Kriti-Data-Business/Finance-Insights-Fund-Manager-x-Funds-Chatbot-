from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
import logging
from datetime import datetime
import os
from typing import Dict, List, Optional

from src.security.auth_manager import AuthManager
from src.data.data_pipeline import DataPipeline
from src.ml.sentiment_analyzer import SentimentAnalyzer
from src.ml.prediction_engine import PredictionEngine
from src.chatbot.conversation_handler import ConversationHandler
from src.monitoring.metrics_collector import MetricsCollector
from src.utils.config import Config
from src.models.schemas import ChatRequest, ChatResponse, HealthResponse

# Initialize FastAPI app with security configurations
app = FastAPI(
    title="Finance Fund Manager Insights Chatbot",
    description="Secure AI-powered chatbot for fund management insights",
    version="1.0.0",
    docs_url=None,  # Disable docs in production
    redoc_url=None  # Disable redoc in production
)

# Security middleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=Config.ALLOWED_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Initialize components
config = Config()
auth_manager = AuthManager(config)
data_pipeline = DataPipeline(config)
sentiment_analyzer = SentimentAnalyzer(config)
prediction_engine = PredictionEngine(config)
conversation_handler = ConversationHandler(config, sentiment_analyzer, prediction_engine)
metrics_collector = MetricsCollector(config)

security = HTTPBearer()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request, call_next):
    start_time = datetime.utcnow()
    response = await call_next(request)
    process_time = (datetime.utcnow() - start_time).total_seconds()
    
    logger.info(f"Request: {request.method} {request.url} - Status: {response.status_code} - Time: {process_time}s")
    await metrics_collector.log_request_metrics(request.method, response.status_code, process_time)
    
    return response

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Validate JWT token and return user info"""
    try:
        user_info = await auth_manager.verify_token(credentials.credentials)
        return user_info
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    user_info: Dict = Depends(get_current_user)
):
    """Main chat endpoint for fund insights"""
    try:
        logger.info(f"Chat request from user {user_info['user_id']}: {request.message[:50]}...")
        
        # Process the conversation
        response = await conversation_handler.process_message(
            message=request.message,
            user_id=user_info['user_id'],
            session_id=request.session_id,
            context=request.context
        )
        
        # Log metrics
        await metrics_collector.log_chat_metrics(
            user_id=user_info['user_id'],
            query_type=response.metadata.get('query_type'),
            response_time=response.metadata.get('response_time'),
            success=True
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Chat processing failed: {str(e)}")
        await metrics_collector.log_chat_metrics(
            user_id=user_info['user_id'],
            query_type="error",
            response_time=0,
            success=False
        )
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    try:
        # Check component health
        pipeline_health = await data_pipeline.health_check()
        ml_health = await sentiment_analyzer.health_check()
        prediction_health = await prediction_engine.health_check()
        
        return HealthResponse(
            status="healthy" if all([pipeline_health, ml_health, prediction_health]) else "unhealthy",
            timestamp=datetime.utcnow(),
            components={
                "data_pipeline": "healthy" if pipeline_health else "unhealthy",
                "sentiment_analyzer": "healthy" if ml_health else "unhealthy",
                "prediction_engine": "healthy" if prediction_health else "unhealthy"
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return HealthResponse(
            status="unhealthy",
            timestamp=datetime.utcnow(),
            components={}
        )

@app.get("/metrics")
async def get_metrics(user_info: Dict = Depends(get_current_user)):
    """Get system metrics (admin only)"""
    if user_info.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return await metrics_collector.get_system_metrics()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        ssl_keyfile=config.SSL_KEY_PATH,
        ssl_certfile=config.SSL_CERT_PATH
    )

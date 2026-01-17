from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """
    Application Configuration using Pydantic Settings.
    Reads from environment variables and .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # Bot
    bot_token: str = Field(..., description="Telegram Bot Token")
    super_admin_id: Optional[int] = Field(None, description="Admin User ID for critical alerts")
    
    # Weather APIs
    qweather_api_key: str = Field(..., description="HeFeng Weather API Key")
    qweather_api_host: str = Field("https://api.qweather.com/v7/", description="HeFeng Weather API Host (Free/Paid)")
    caiyun_api_token: str = Field(..., description="Caiyun Weather API Token")

    
    # Infrastructure
    redis_url: str = Field("redis://localhost:6379/0", description="Redis Connection URL")
    
    # Logging
    log_level: str = Field("DEBUG", description="Logging Level")
    
    # Feature Flags
    enable_caiyun_minutely: bool = True
    enable_weather_plots: bool = True

    # LLM Service
    llm_provider: str = Field("openai", description="LLM Provider: 'openai' or 'gemini'")
    llm_model: Optional[str] = Field(None, description="Model Name (e.g. gpt-4o, gemini-1.5-flash)")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API Key")
    openai_api_base: Optional[str] = Field(None, description="OpenAI API Base URL")
    gemini_api_key: Optional[str] = Field(None, description="Google Gemini API Key")
    gemini_api_base: Optional[str] = Field(None, description="Google Gemini API Base URL")
    llm_streaming: bool = True

settings = Settings()

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

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
    qweather_api_host: str = Field("https://api.qweather.com", description="QWeather API root host")
    qweather_daily_days: str = Field("7d", description="QWeather daily forecast range: 3d, 7d, 10d, 15d, 30d")
    qweather_hourly_hours: str = Field("24h", description="QWeather hourly forecast range: 24h, 72h, 168h")
    qweather_indices_types: str = Field("1,2,3,5,9", description="QWeather life index type ids")
    qweather_enable_minutely: bool = Field(True, description="Enable QWeather minutely precipitation")
    caiyun_api_token: Optional[str] = Field(None, description="Caiyun Weather API Token")

    
    # Infrastructure
    redis_url: str = Field("redis://localhost:6379/0", description="Redis Connection URL")
    
    # Logging
    log_level: str = Field("DEBUG", description="Logging Level")
    
    # Feature Flags
    enable_caiyun_api: bool = Field(False, description="Enable Caiyun as an optional rain enhancement source")
    enable_caiyun_minutely: bool = Field(False, description="Deprecated alias for ENABLE_CAIYUN_API")
    enable_weather_plots: bool = True

    # LLM Service
    llm_provider: str = Field("openai", description="LLM Provider: 'openai' or 'gemini'")
    llm_model: Optional[str] = Field(None, description="Legacy fallback model name")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API Key")
    openai_api_base: Optional[str] = Field(None, description="OpenAI API Base URL")
    openai_model: Optional[str] = Field(None, description="OpenAI model name")
    openai_api_mode: str = Field("responses", description="OpenAI API mode: 'responses' or 'chat_completions'")
    openai_reasoning_effort: str = Field("medium", description="OpenAI reasoning effort")
    openai_verbosity: str = Field("medium", description="OpenAI response verbosity")
    openai_temperature: Optional[float] = Field(None, description="Optional OpenAI temperature")
    openai_timeout_seconds: float = Field(40.0, description="OpenAI HTTP request timeout in seconds")
    openai_max_output_tokens: Optional[int] = Field(900, description="Optional OpenAI max output tokens for weather reports")
    gemini_api_key: Optional[str] = Field(None, description="Google Gemini API Key")
    gemini_api_base: Optional[str] = Field(None, description="Google Gemini API Base URL")
    gemini_model: Optional[str] = Field(None, description="Google Gemini model name")
    llm_streaming: bool = True
    llm_report_timeout_seconds: float = Field(35.0, description="AI weather report generation timeout in seconds")
    llm_weather_report_prompt: Optional[str] = Field(None, description="Custom system prompt for AI weather reports")
    llm_weather_report_prompt_file: Optional[str] = Field(None, description="Path to a custom AI weather report prompt file")

    @field_validator("llm_model", "openai_model", "gemini_model", "llm_weather_report_prompt_file", mode="before")
    @classmethod
    def normalize_optional_string(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("llm_weather_report_prompt", mode="before")
    @classmethod
    def normalize_optional_prompt(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            return value.replace("\\n", "\n")
        return value

    @field_validator("openai_temperature", mode="before")
    @classmethod
    def normalize_optional_temperature(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_max_output_tokens", mode="before")
    @classmethod
    def normalize_optional_int(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("qweather_api_host")
    @classmethod
    def normalize_qweather_api_host(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        for suffix in ("/v7", "/geo/v2"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    @field_validator("caiyun_api_token", mode="before")
    @classmethod
    def normalize_optional_token(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("qweather_daily_days")
    @classmethod
    def validate_qweather_daily_days(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"3d", "7d", "10d", "15d", "30d"}
        if value not in allowed:
            raise ValueError(f"qweather_daily_days must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("qweather_hourly_hours")
    @classmethod
    def validate_qweather_hourly_hours(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"24h", "72h", "168h"}
        if value not in allowed:
            raise ValueError(f"qweather_hourly_hours must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("qweather_indices_types")
    @classmethod
    def normalize_qweather_indices_types(cls, value: str) -> str:
        values = [part.strip() for part in value.split(",") if part.strip()]
        if not values:
            raise ValueError("qweather_indices_types must not be empty")
        return ",".join(values)

    @field_validator("openai_api_mode")
    @classmethod
    def validate_openai_api_mode(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"responses", "chat_completions"}
        if value not in allowed:
            raise ValueError(f"openai_api_mode must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("openai_reasoning_effort")
    @classmethod
    def validate_openai_reasoning_effort(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
        if value not in allowed:
            raise ValueError(f"openai_reasoning_effort must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("openai_verbosity")
    @classmethod
    def validate_openai_verbosity(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"low", "medium", "high"}
        if value not in allowed:
            raise ValueError(f"openai_verbosity must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("openai_timeout_seconds", "llm_report_timeout_seconds")
    @classmethod
    def validate_positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout must be greater than 0")
        return value

    @field_validator("openai_max_output_tokens")
    @classmethod
    def validate_openai_max_output_tokens(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value <= 0:
            raise ValueError("openai_max_output_tokens must be greater than 0")
        return value

    # Webhook Configuration
    bot_mode: str = Field("polling", description="Bot运行模式: 'polling' 或 'webhook'")
    webhook_url: Optional[str] = Field(None, description="Webhook URL (e.g. https://yourdomain.com)")
    webhook_port: int = Field(8443, description="Webhook 监听端口")
    webhook_path: str = Field("/webhook", description="Webhook 路径")
    webhook_secret: Optional[str] = Field(None, description="Webhook Secret Token (可选，增强安全性)")

settings = Settings()

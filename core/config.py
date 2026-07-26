from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator


DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"

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
    qweather_daily_days: str = Field("15d", description="QWeather daily forecast range: 3d, 7d, 10d, 15d, 30d")
    qweather_hourly_hours: str = Field("72h", description="QWeather hourly forecast range: 24h, 72h, 168h")
    # All 16 Chinese life indices: the renderers already group every type and
    # QWeather is free, so requesting a subset only hid data from users.
    qweather_indices_types: str = Field(
        "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16",
        description="QWeather life index type ids (China supports 1-16)",
    )
    qweather_enable_minutely: bool = Field(True, description="Enable QWeather minutely precipitation")
    qweather_indices_days: str = Field("3d", description="QWeather life index range: 1d or 3d")
    enable_grid_weather: bool = Field(
        True,
        description="Use QWeather grid (numerical model) weather when the nearest city is too far from the requested coordinates",
    )
    grid_weather_distance_km: float = Field(
        15.0,
        description="Switch to grid weather when the geocoded city is farther than this from the requested coordinates",
    )
    enable_history_comparison: bool = Field(
        True,
        description="Fetch yesterday's observed summary (Time Machine) so the AI report can compare day over day",
    )

    @field_validator("qweather_indices_days")
    @classmethod
    def validate_qweather_indices_days(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"1d", "3d"}:
            raise ValueError("qweather_indices_days must be 1d or 3d")
        return value

    @field_validator("grid_weather_distance_km")
    @classmethod
    def validate_grid_distance(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("grid_weather_distance_km must be greater than 0")
        return value
    caiyun_api_token: Optional[str] = Field(None, description="Caiyun Weather API Token")
    caiyun_cache_ttl_seconds: int = Field(3600, description="Caiyun successful response cache TTL")
    caiyun_failure_cooldown_seconds: int = Field(60, description="Caiyun temporary failure cooldown")
    caiyun_hourly_steps: int = Field(72, description="Caiyun hourly forecast steps")
    caiyun_daily_steps: int = Field(15, description="Caiyun daily forecast steps")

    
    # Infrastructure
    redis_url: str = Field("redis://localhost:6379/0", description="Redis Connection URL")
    
    # Logging
    log_level: str = Field("INFO", description="Logging Level")
    
    # Feature Flags
    enable_caiyun_api: bool = Field(False, description="Enable Caiyun as an optional rain enhancement source")
    enable_caiyun_minutely: bool = Field(False, description="Deprecated alias for ENABLE_CAIYUN_API")
    enable_weather_plots: bool = True
    enable_rain_alerts: bool = Field(True, description="Enable scheduled rain alert checks")
    enable_daily_brief: bool = Field(True, description="Enable scheduled daily brief pushes")
    enable_rich_messages: bool = Field(
        True,
        description="Use Bot API 10.1/10.2 rich messages (tables, collapsible details); falls back to MarkdownV2/HTML automatically",
    )
    enable_ephemeral_messages: bool = Field(
        True,
        description="Use Bot API 10.2 ephemeral messages for personal replies in group chats",
    )
    enable_rich_report_streaming: bool = Field(
        True,
        description="Stream AI reports with sendRichMessageDraft in private chats (native animated preview)",
    )

    # Scheduling
    timezone: str = Field("Asia/Shanghai", description="Timezone for scheduled pushes (daily brief)")
    rain_alert_cooldown_hours: float = Field(4.0, description="Minimum hours between rain alerts per chat+location")
    rain_alert_quiet_hours: str = Field(
        "23:00-07:00",
        description="Quiet window (HH:MM-HH:MM in TIMEZONE) during which rain checks pause; empty disables",
    )
    max_subscriptions_per_chat: int = Field(3, description="Max subscribed cities per chat per subscription type")
    rain_check_interval_minutes: int = Field(30, description="Minutes between scheduled rain checks")
    enable_alert_push: bool = Field(True, description="Push official weather warnings to rain-alert subscribers")
    alert_check_interval_minutes: int = Field(10, description="Minutes between official warning / derived event checks")
    alert_quiet_hours_exempt_levels: str = Field(
        "红色,橙色",
        description="Warning levels that ignore quiet hours (life-safety); comma separated, empty means none",
    )
    enable_derived_event_alerts: bool = Field(
        True,
        description="Also alert on derived events: air quality deterioration, extreme temperature, strong wind",
    )
    aqi_alert_threshold: int = Field(150, description="AQI at or above which an air-quality alert fires")
    high_temp_alert_threshold: float = Field(35.0, description="Daily high (°C) at or above which a heat alert fires")
    low_temp_alert_threshold: float = Field(-5.0, description="Daily low (°C) at or below which a cold alert fires")
    wind_alert_scale_threshold: int = Field(6, description="Wind scale at or above which a wind alert fires")
    daily_brief_catchup_hours: float = Field(
        2.0,
        description="Send a missed daily brief if the bot comes back within this window; 0 disables catch-up",
    )
    enable_rate_limiter: bool = Field(True, description="Use PTB's AIORateLimiter to respect Telegram flood limits")
    persistence_backup_count: int = Field(3, description="Rotated copies of the persistence file kept at startup; 0 disables")

    @field_validator("alert_check_interval_minutes")
    @classmethod
    def validate_alert_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("alert_check_interval_minutes must be at least 1")
        return value

    @field_validator("daily_brief_catchup_hours")
    @classmethod
    def validate_catchup_hours(cls, value: float) -> float:
        if value < 0:
            raise ValueError("daily_brief_catchup_hours must be >= 0")
        return value

    @field_validator("persistence_backup_count")
    @classmethod
    def validate_backup_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("persistence_backup_count must be >= 0")
        return value

    @property
    def alert_exempt_levels(self) -> set[str]:
        return {part.strip() for part in self.alert_quiet_hours_exempt_levels.split(",") if part.strip()}

    @field_validator("rain_check_interval_minutes")
    @classmethod
    def validate_rain_check_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("rain_check_interval_minutes must be at least 1")
        return value

    @field_validator("rain_alert_cooldown_hours")
    @classmethod
    def validate_rain_alert_cooldown(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("rain_alert_cooldown_hours must be greater than 0")
        return value

    @field_validator("rain_alert_quiet_hours", mode="before")
    @classmethod
    def validate_rain_alert_quiet_hours(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return ""
            from utils.schedule_times import parse_quiet_hours

            if parse_quiet_hours(value) is None:
                raise ValueError("rain_alert_quiet_hours must look like 23:00-07:00 (or empty to disable)")
        return value

    @field_validator("max_subscriptions_per_chat")
    @classmethod
    def validate_max_subscriptions(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_subscriptions_per_chat must be at least 1")
        return value

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
    openai_timeout_seconds: float = Field(60.0, description="OpenAI HTTP request timeout in seconds")
    openai_max_output_tokens: Optional[int] = Field(None, description="Optional OpenAI max output tokens for weather reports; empty = provider default")
    gemini_api_key: Optional[str] = Field(None, description="Google Gemini API Key")
    gemini_api_base: Optional[str] = Field(None, description="Google Gemini API Base URL")
    gemini_model: Optional[str] = Field(None, description="Google Gemini model name")
    gemini_timeout_seconds: float = Field(60.0, description="Gemini HTTP request timeout in seconds")
    llm_report_timeout_seconds: float = Field(60.0, description="AI weather report generation timeout in seconds")
    llm_streaming: bool = Field(True, description="Stream AI reports and progressively edit the Telegram message")
    llm_report_cache_ttl_seconds: int = Field(
        14400,
        description="AI weather report cache TTL in seconds; 0 disables the cache",
    )
    llm_weather_report_prompt: Optional[str] = Field(None, description="Custom system prompt for AI weather reports")
    llm_weather_report_prompt_file: Optional[str] = Field(None, description="Path to a custom AI weather report prompt file")

    @field_validator(
        "llm_model",
        "openai_model",
        "gemini_model",
        "openai_api_key",
        "openai_api_base",
        "gemini_api_key",
        "gemini_api_base",
        "llm_weather_report_prompt_file",
        "webhook_url",
        "webhook_secret",
        mode="before",
    )
    @classmethod
    def normalize_optional_string(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("bot_token", "qweather_api_key", mode="before")
    @classmethod
    def validate_required_non_empty_string(cls, value):
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
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

    @field_validator(
        "caiyun_cache_ttl_seconds",
        "caiyun_failure_cooldown_seconds",
        "caiyun_hourly_steps",
        "caiyun_daily_steps",
    )
    @classmethod
    def validate_positive_caiyun_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Caiyun cache and step settings must be greater than 0")
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

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"openai", "gemini"}
        if value not in allowed:
            raise ValueError(f"llm_provider must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("openai_reasoning_effort")
    @classmethod
    def validate_openai_reasoning_effort(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        if value not in allowed:
            raise ValueError(f"openai_reasoning_effort must be one of: {', '.join(sorted(allowed))}")
        return value

    @model_validator(mode="after")
    def validate_openai_model_reasoning_pair(self):
        effective_model = self.openai_model or self.llm_model or DEFAULT_OPENAI_MODEL
        if effective_model.startswith("gpt-5.6") and self.openai_reasoning_effort == "minimal":
            raise ValueError("GPT-5.6 does not support minimal reasoning effort; use none or low")
        return self

    @field_validator("openai_verbosity")
    @classmethod
    def validate_openai_verbosity(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"low", "medium", "high"}
        if value not in allowed:
            raise ValueError(f"openai_verbosity must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("openai_timeout_seconds", "llm_report_timeout_seconds", "gemini_timeout_seconds")
    @classmethod
    def validate_positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout must be greater than 0")
        return value

    @field_validator("llm_report_cache_ttl_seconds")
    @classmethod
    def validate_report_cache_ttl(cls, value: int) -> int:
        if value < 0:
            raise ValueError("llm_report_cache_ttl_seconds must be >= 0 (0 disables the cache)")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception as e:
            raise ValueError(f"invalid timezone: {value}") from e
        return value

    @model_validator(mode="after")
    def fold_deprecated_caiyun_flag(self):
        if self.enable_caiyun_minutely and not self.enable_caiyun_api:
            import warnings

            warnings.warn(
                "ENABLE_CAIYUN_MINUTELY is deprecated; use ENABLE_CAIYUN_API instead",
                DeprecationWarning,
                stacklevel=2,
            )
            self.enable_caiyun_api = True
        return self

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

    @field_validator("bot_mode")
    @classmethod
    def validate_bot_mode(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"polling", "webhook"}
        if value not in allowed:
            raise ValueError(f"bot_mode must be one of: {', '.join(sorted(allowed))}")
        return value

    @field_validator("webhook_path")
    @classmethod
    def normalize_webhook_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("webhook_path must not be empty")
        return value if value.startswith("/") else f"/{value}"

    @field_validator("webhook_port")
    @classmethod
    def validate_webhook_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("webhook_port must be between 1 and 65535")
        return value

settings = Settings()

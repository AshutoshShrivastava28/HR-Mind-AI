from app.core.config import get_settings

settings = get_settings()

print(f"App Name : {settings.app_name}")
print(f"Open AI Key : {settings.openai_api_key}")
print(f"Open AI Model : {settings.openai_model}")

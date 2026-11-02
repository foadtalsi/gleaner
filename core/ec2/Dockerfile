FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.128.8 pydantic==2.13.5 uvicorn==0.39.0 boto3==1.42.97 requests==2.32.5

COPY task_manager_api.py scheduler.py instances.py waste.py llm.py users.py agent_key.py index.html ./

CMD ["uvicorn", "task_manager_api:app", "--host", "0.0.0.0", "--port", "8000"]

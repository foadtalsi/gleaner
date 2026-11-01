# gleaner agent image
#
# Build:
#   docker build -t gleaner-agent agent/ec2
#
# Run:
#   docker run -d --name gleaner-agent --restart unless-stopped \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -e CORE_URL=http://host.docker.internal:8000 \
#     -e INSTANCE_ID=local-dev \
#     -e QUEUE_URL=<SQS queue url> \
#     -e TASK_IMAGE=gleaner-agent \
#     -e AWS_DEFAULT_REGION=us-east-1 \
#     gleaner-agent
#
# The same image also runs the tasks: the worker starts it with python -c <code written by the LLM>, as nobody.
# The Docker socket is mounted so the worker can start and kill the task containers.
# Warning: it gives the agent root-equivalent access to the machine.
# On Mac, host.docker.internal points to the machine running Docker; on an EC2, set the real core URL.

FROM python:3.12-slim

WORKDIR /app

# dependencies first, so they aren't reinstalled on every code change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py worker.py ./

# default values, to override at launch with -e
ENV CORE_URL=http://localhost:8000 \
    INSTANCE_ID=local-dev \
    INTERVAL=5

CMD ["python", "agent.py"]

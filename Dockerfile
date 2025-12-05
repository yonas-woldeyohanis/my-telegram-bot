# CHANGE THIS LINE: Use Python 3.10 instead of 3.9
FROM python:3.10-slim

# Install FFmpeg AND Git
RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Run the bot
CMD ["python", "main.py"]
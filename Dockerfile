# Dockerfile for yt-live.py YouTube streaming backend
# Use official Python image as base
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy project files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir yt-dlp flask

# Expose Flask port
EXPOSE 5000

# Default command
CMD ["python", "yt-live.py"]

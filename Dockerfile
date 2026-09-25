# Use Ubuntu as the base
FROM ubuntu:22.04

# Install dependencies
RUN apt update && apt install -y curl python3 python3-pip

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Pull the model you want
RUN ollama pull llama3

# Copy your bot files
WORKDIR /app
COPY . .

# Install Python requirements
RUN pip install -r requirements.txt

# Expose Ollama port
EXPOSE 11434

# Start Ollama and your bot
CMD ollama serve & python3 main.py

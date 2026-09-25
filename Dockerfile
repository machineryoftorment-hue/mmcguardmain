FROM ubuntu:22.04

# Install dependencies
RUN apt update && apt install -y curl python3 python3-pip

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Pull the model
RUN ollama pull llama3

# Copy bot files
WORKDIR /app
COPY . .

# Install Python requirements
RUN pip install -r requirements.txt

# Expose ports
EXPOSE 8080
EXPOSE 11434

# Start Ollama in background, then run main.py (Flask stays foreground)
CMD bash -c "ollama serve & sleep 5 && python3 main.py"

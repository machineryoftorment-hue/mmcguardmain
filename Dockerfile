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

# Expose Ollama + health port
EXPOSE 11434
EXPOSE 8080

# Start Ollama, wait, then run bot (health server starts inside main.py)
CMD bash -c "ollama serve & sleep 10 && python3 main.py"

# Qwen3 14B served by Ollama on a Cloud Run GPU. The model is built into the image so a cold start only loads it.
FROM ollama/ollama:0.40.16
ENV OLLAMA_HOST=0.0.0.0:8080 \
    OLLAMA_MODELS=/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NUM_PARALLEL=4 \
    OLLAMA_FLASH_ATTENTION=1
RUN ollama serve & pid=$!; sleep 5; ollama pull qwen3:14b && kill $pid
ENTRYPOINT ["ollama", "serve"]

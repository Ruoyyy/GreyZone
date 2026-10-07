FROM python:3.11-slim
RUN pip install --no-cache-dir starlette==1.7.0 uvicorn==0.54.0
WORKDIR /sim
COPY sim.py seed.py ui.html ./
EXPOSE 9000
CMD ["uvicorn", "sim:app", "--host", "0.0.0.0", "--port", "9000"]

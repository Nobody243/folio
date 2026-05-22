@echo off
echo ========================================================
echo Starting Big Data Clothing Aggregator
echo ========================================================
echo.

echo [1/3] Starting Kafka and Spark Streaming (Docker)...
docker-compose up -d

echo.
echo [2/3] Waiting for Kafka to boot up (10 seconds)...
timeout /t 10 /nobreak

echo.
echo [3/3] Starting FastAPI Backend...
:: Using 'start cmd /k' opens a new terminal window for the backend so it runs concurrently
start cmd /k "python -m uvicorn backend.api:app --reload"

echo.
echo [4/4] Starting React Frontend...
:: Opens a new terminal window for the frontend
start cmd /k "npm run dev"

echo.
echo [5/5] Opening website in default browser...
timeout /t 3 /nobreak
start http://localhost:5173

echo.
echo All services are launching!
echo Note: The backend terminal will show an error and restart if Kafka is not fully up yet.
echo This is normal; it will connect successfully once Kafka is ready.
echo ========================================================
pause

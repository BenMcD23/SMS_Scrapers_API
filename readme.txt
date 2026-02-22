uvicorn api:app --reload

docker compose up -d
docker exec -d tailscale tailscale funnel http://172.18.0.2:8000

docker compose down

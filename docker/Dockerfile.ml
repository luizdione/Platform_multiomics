# omica-ml — worker da fila "ml" (classificação, SHAP, painéis mínimos).
# Chegou na fase prevista pelo plano ("Dockerfile.worker separado chega na fase
# de ML"). Pins conservadores: numpy 1.26 evita o inferno de resolução
# numba/shap; sklearn/xgboost/shap são os mesmos motores do
# bovine_aim_gwas_ml_thermalcore (migração curada).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMICA_IN_DOCKER=1 \
    PYTHONPATH=/app

RUN pip install \
    rq==1.16.2 redis==5.2.1 psutil==6.1.0 PyYAML==6.0.2 pydantic==2.10.3 \
    numpy==1.26.4 pandas==2.2.3 scikit-learn==1.5.2 xgboost==2.1.3 \
    shap==0.46.0 matplotlib==3.9.2 pytest==8.3.4

WORKDIR /app
CMD ["rq", "worker", "ml", "--url", "redis://omica-redis:6379/0"]

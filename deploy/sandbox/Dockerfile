FROM sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.1.0
SHELL ["/bin/bash", "-c"]
RUN source /opt/code-interpreter/code-interpreter-env.sh python 3.11 && \
    python -m pip install --no-cache-dir --target /opt/paperagent-packages \
      numpy==2.3.5 pandas==2.3.3 scipy==1.16.3 matplotlib==3.10.7 \
      scikit-learn==1.7.2 seaborn==0.13.2 openpyxl==3.1.5 sympy==1.14.0
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*
ENV MPLBACKEND=Agg PYTHONUNBUFFERED=1 PYTHONPATH=/opt/paperagent-packages

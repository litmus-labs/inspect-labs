FROM mambaorg/micromamba:2.3.2
USER root
RUN mkdir -p /work && chown mambauser:mambauser /work
USER mambauser
RUN micromamba install -y -n base -c conda-forge -c bioconda \
    python=3.12 biopython numpy pandas pyyaml pycountry packaging \
    flask openpyxl xlrd werkzeug psutil pip git 'blast>=2.17' hmmer infernal zstd \
    && micromamba clean --all --yes
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-deps git+https://github.com/ibbis-bio/common-mechanism.git@24fe0390978eb66d13db4f91c18f122d051b1a65 \
    && micromamba list --explicit > /opt/conda/commec-environment.lock
ENV PATH=/opt/conda/bin:$PATH
WORKDIR /work
CMD ["sleep", "infinity"]

FROM dmoj/runtimes-tier3

RUN apt-get update && \
    apt-get -y --no-install-recommends install make cmake git gcc-multilib && \
    rm -rf /var/lib/apt/lists/*

RUN cd /tmp/ && git clone https://github.com/justinmeza/lci.git && \
    cd lci && \
    cmake . && \
    make && \
    make install && \
    rm -rf /tmp/lci/

RUN git clone --recursive https://github.com/MCPT/WLMOJ-judge judge
# COPY WLMOJ-judge judge/
WORKDIR /judge/
RUN pip3 install -e . && \
    dmoj-autoconf -V > /judge-runtime-paths.yml && \
    echo '  crt_x86_in_lib32: true' >> /judge-runtime-paths.yml

ENTRYPOINT ["/judge/.docker/entry"]

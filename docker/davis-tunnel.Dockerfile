FROM scratch
COPY cloudflared /usr/local/bin/cloudflared
COPY cloudflared-ca.crt /etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
USER 65532:65532
ENTRYPOINT ["cloudflared"]

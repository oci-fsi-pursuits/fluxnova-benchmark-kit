ARG JAVA_IMAGE
FROM ${JAVA_IMAGE}
WORKDIR /opt/fluxnova
COPY dist/fluxnova.zip /tmp/fluxnova.zip
COPY dist/opentelemetry-javaagent.jar /opt/opentelemetry-javaagent.jar
# Corretto contains the JDK jar utility: no mutable OS package install is needed.
RUN jar xf /tmp/fluxnova.zip && rm /tmp/fluxnova.zip
COPY deploy/start-fluxnova.sh /opt/fluxnova/benchmark-start.sh
RUN chmod 0755 /opt/fluxnova/benchmark-start.sh && chown -R 10001:10001 /opt/fluxnova
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/opt/fluxnova/benchmark-start.sh"]

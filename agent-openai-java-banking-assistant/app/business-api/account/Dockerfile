FROM mcr.microsoft.com/openjdk/jdk:17-ubuntu@sha256:5194c4ae4fdf9343e5d099c527cd61ac0b2d651a8fab5f90aaafe0c956112417 AS build

WORKDIR /workspace/app
EXPOSE 3100

COPY mvnw .
COPY .mvn .mvn
COPY pom.xml .
COPY src src

RUN chmod +x ./mvnw
# Convert CRLF to LF
RUN sed -i 's/\r$//' ./mvnw
RUN ./mvnw package -DskipTests
RUN mkdir -p target/dependency && (cd target/dependency; jar -xf ../*.jar)

ADD --checksum=sha256:4d49a6a3bfc223cf36d5e41b430e1a732d27b920f29619b34fab933c3f7ecc08 https://github.com/microsoft/ApplicationInsights-Java/releases/download/3.5.4/applicationinsights-agent-3.5.4.jar applicationinsights-agent-3.5.4.jar
COPY applicationinsights.json .

#for production deployment use mcr.microsoft.com/openjdk/jdk:17-distroless
FROM mcr.microsoft.com/openjdk/jdk:17-distroless@sha256:c05ef251261823dc54a23cf199c254c8d4c62c4e382d63ebac72568782293f24

ARG DEPENDENCY=/workspace/app/target/dependency
COPY --from=build ${DEPENDENCY}/BOOT-INF/lib /app/lib
COPY --from=build ${DEPENDENCY}/META-INF /app/META-INF
COPY --from=build ${DEPENDENCY}/BOOT-INF/classes /app
COPY --from=build /workspace/app/applicationinsights-agent-3.5.4.jar /app
COPY --from=build /workspace/app/applicationinsights.json /app

EXPOSE 8080

ENTRYPOINT ["java","-javaagent:/app/applicationinsights-agent-3.5.4.jar","-noverify", "-XX:MaxRAMPercentage=70", "-XX:+UseParallelGC", "-XX:ActiveProcessorCount=2", "-cp","app:app/lib/*","com.microsoft.openai.samples.assistant.business.AccountApplication"]
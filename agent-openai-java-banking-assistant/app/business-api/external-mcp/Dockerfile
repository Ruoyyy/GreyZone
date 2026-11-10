FROM maven:3.9-eclipse-temurin-17@sha256:1a352420f7aba21f5ad08df31bab55f74c013fb491f1ae8ab1dd7ff9ed698584 AS build
WORKDIR /workspace
COPY pom.xml .
COPY src src
RUN mvn -q -DskipTests package

FROM eclipse-temurin:17-jre@sha256:2a996a534890f1bee252fab23a0c01e773e9e1dc0e4700e8f0f6a2697c6cbf76
WORKDIR /app
COPY --from=build /workspace/target/external-mcp-*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app/app.jar"]

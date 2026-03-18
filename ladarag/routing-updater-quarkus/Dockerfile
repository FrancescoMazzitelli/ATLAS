FROM quay.io/quarkus/ubi-quarkus-mandrel-builder-image:jdk-21 AS build

WORKDIR /project

COPY . .

RUN ./mvnw package -Dnative -DskipTests

FROM registry.access.redhat.com/ubi9/ubi-minimal:9.6

WORKDIR /work/

RUN microdnf install -y \
    tar \
    gzip \
    findutils \
    && microdnf clean all

RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/ \
  | grep -o 'docker-[0-9.]*.tgz' \
  | sort -V \
  | tail -n1 \
  | xargs -I {} curl -fsSL https://download.docker.com/linux/static/stable/x86_64/{} \
  | tar -xz -C /usr/bin --strip-components=1 docker/docker

COPY --from=build /project/target/*-runner /work/application

RUN chmod +x /work/application

EXPOSE 8080

ENTRYPOINT ["./application", "-Dquarkus.http.host=0.0.0.0"]
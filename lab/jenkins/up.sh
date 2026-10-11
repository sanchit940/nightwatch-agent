#!/usr/bin/env bash
# Local Jenkins for the NightWatch CI connector (lab only: no security, port 8090).
# Docker Desktop has about 2 GB: pause the kind cluster first (docker stop nightwatch-control-plane).
set -euo pipefail
cd "$(dirname "$0")"
if docker ps -a --format '{{.Names}}' | grep -qx jenkins-lab; then
  docker start jenkins-lab >/dev/null
else
  docker run -d --name jenkins-lab -p 8090:8080 --memory 700m \
    -e JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Xmx384m" jenkins/jenkins:2.479.3-lts-jdk17 >/dev/null
fi
until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/json)" = "200" ]; do sleep 5; done
CJ=$(mktemp)
CRUMB=$(curl -s -c "$CJ" localhost:8090/crumbIssuer/api/json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])")
if [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8090/job/nightwatch-lab-build/api/json)" != "200" ]; then
  curl -s -b "$CJ" -X POST -H "$CRUMB" -H "Content-Type: application/xml" --data-binary @shop-api-job.xml \
    "localhost:8090/createItem?name=nightwatch-lab-build" >/dev/null
fi
echo "Jenkins: http://localhost:8090  job: nightwatch-lab-build  (set JENKINS_URL=http://localhost:8090)"
echo "Good build:  curl -X POST -H \"\$CRUMB\" 'localhost:8090/job/nightwatch-lab-build/buildWithParameters?PROFILE=standard'"
echo "Profiles: standard (passes), tax-v2 (a unit test fails), publish-only (registry credential missing)"

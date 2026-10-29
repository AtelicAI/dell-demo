#!/bin/bash
# Push the repo to the AWS host and (re)start the stack. Usage: deploy/aws/deploy.sh [--seed]
# Needs .deploy/key.pem and .deploy/aws.env (deploy/gen_env.sh). Host from the CloudFormation stack.
set -euo pipefail
cd "$(dirname "$0")/../.."
STACK=${STACK:-atelic-dell-demo}
IP=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='PublicIp'].OutputValue" --output text)
SSH=(ssh -i .deploy/key.pem -o StrictHostKeyChecking=accept-new ec2-user@"$IP")
rsync -az --delete -e "ssh -i .deploy/key.pem" \
  --exclude .git --exclude .deploy --exclude .run --exclude .ui-state --exclude '__pycache__' --exclude 'engine/backend/tests' \
  ./ ec2-user@"$IP":/opt/atelic/
scp -q -i .deploy/key.pem .deploy/aws.env ec2-user@"$IP":/opt/atelic/.env
"${SSH[@]}" "cd /opt/atelic && chmod 600 .env && docker compose -f docker-compose.yml -f deploy/compose.aws.yml up -d --build --remove-orphans"
if [[ "${1:-}" == "--seed" ]]; then
  "${SSH[@]}" "cd /opt/atelic && docker compose -f docker-compose.yml -f deploy/compose.aws.yml run --rm seed && docker compose -f docker-compose.yml -f deploy/compose.aws.yml restart engine backend"
fi
echo "Deployed: https://$(echo "$IP" | tr . -).sslip.io"

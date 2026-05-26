#!/bin/sh
# Wait for Docker socket to be available
while [ ! -S /var/run/docker.sock ]; do
    echo "Waiting for Docker socket..."
    sleep 2
done

# Give Docker some time to initialize
sleep 10

# Now start autoheal
exec /docker-entrypoint autoheal

APP    ?= birdnet-analytics
DOMAIN ?= sgc.rayandhon.com
HOST   ?= $(APP).$(DOMAIN)

.PHONY: deploy preview logs stop

deploy:
	COMPOSE_PROJECT_NAME=$(APP) \
	SERVICE_HOST=$(HOST) \
	docker compose up -d --build

preview:
	COMPOSE_PROJECT_NAME=$(APP)-preview \
	SERVICE_HOST=$(APP)-preview.$(DOMAIN) \
	docker compose up -d --build

logs:
	docker compose -p $(APP) logs -f

stop:
	docker compose -p $(APP) down

# The fleet's gate (reverse-proxy make/fleet.mk, which includes the checkout
# guard): `make deploy` runs checkout-guard and fleet-check against this repo's
# fleet.yml first, and `make install-units` links deploy units (UNIT-1).
# Optional include: CI and other machines have no reverse-proxy checkout, and a
# plain include would stop make before any target runs. Without it, deploy
# refuses rather than running ungated.
FLEET_MK := $(HOME)/dev/reverse-proxy/make/fleet.mk
-include $(FLEET_MK)
ifeq ($(wildcard $(FLEET_MK)),)
_FLEET_SAVED_GOAL := $(.DEFAULT_GOAL)
.PHONY: fleet-missing
fleet-missing:
	@echo "deploy refused: $(FLEET_MK) not found (no reverse-proxy checkout here)" >&2; exit 1
deploy install-units: fleet-missing
.DEFAULT_GOAL := $(_FLEET_SAVED_GOAL)
endif

APP    ?= birdnet-analytics
DOMAIN ?= sgc.rayandhon.com
HOST   ?= $(APP).$(DOMAIN)

.PHONY: deploy deploy-pulled preview logs stop

deploy:
	git pull
	@# A fresh make, so the rest runs the recipe just pulled, not this one
	@# (reverse-proxy#78): make reads the Makefile before the pull.
	@$(MAKE) --no-print-directory deploy-pulled

# The part of deploy after the pull. Run `make deploy`, not this.
deploy-pulled:
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

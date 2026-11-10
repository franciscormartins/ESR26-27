# Atalhos para o ottctl e o painel. `make help` lista os alvos.
# Variáveis: CEN=cenario4  PORT=8080  BACKEND=local|core  SESSION=N (sessão do CORE)  ARGS="..."
CEN     ?= cenario4
PORT    ?= 8080
BACKEND ?= local
SESSION ?=
PY      ?= python3
CTL     := ./bin/ottctl
SESS    := $(if $(SESSION),--session $(SESSION),)

.PHONY: help dashboard dashboard-core up up-core down status events test test-core test-md unit list clean

help:
	@echo "make dashboard        painel web em http://127.0.0.1:$(PORT) (CEN, modo local; muda de cenário no painel)"
	@echo "make dashboard-core   idem ligado a uma sessão do CORE já iniciada (SESSION=N opcional)"
	@echo "make up | down        lança / pára os nós de CEN ($(CEN)) em modo BACKEND=$(BACKEND)"
	@echo "make status | events estado e eventos de CEN"
	@echo "make test             testes automáticos de CEN (modo local); make test-core no CORE; ARGS='A1 A2 --repeat 3'"
	@echo "make test-md          testes de CEN com tabela em Markdown para o relatório"
	@echo "make unit             testes unitários (python unittest)"
	@echo "make list             cenários com plano"

dashboard:
	$(CTL) dashboard $(CEN) --backend local --port $(PORT) $(ARGS)

dashboard-core:
	$(CTL) dashboard $(CEN) --backend core $(SESS) --port $(PORT) $(ARGS)

up:
	$(CTL) up $(CEN) --backend $(BACKEND) $(SESS) $(ARGS)

up-core:
	$(CTL) up $(CEN) --backend core $(SESS) $(ARGS)

down:
	$(CTL) down $(CEN)

status:
	$(CTL) status $(CEN)

events:
	$(CTL) events $(CEN) -n 40

test:
	$(CTL) test $(CEN) $(ARGS)

test-core:
	$(CTL) test $(CEN) $(ARGS)

test-md:
	$(CTL) test $(CEN) --md $(ARGS)

unit:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -t .

list:
	$(CTL) list

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

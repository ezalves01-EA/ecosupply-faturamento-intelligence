# Ecosupply Faturamento Intelligence — EFI v0.2

Dashboard de análise de faturamento por cidade, região, filial, transportadora, motorista, placa e tipo de veículo.

## Situação
- Frota recuperada do projeto Ecosupply_logsitica_BI: **72 IDs**, **66 placas distintas**, 10 tipos de veículo.
- Dados financeiros: **não importados**. O sistema não inventa faturamentos.
- Integração automática SISGR: **pendente** da validação faturamento → BM → item → coleta/agendamento/serviço.
- Importação manual CSV disponível em /import/revenue.

## Execução local
```bash
pip install -r requirements.txt
export APP_PASSWORD='defina-uma-senha-forte'
python app.py
```
Acesse http://localhost:8000 com autenticação HTTP Basic (usuário livre, senha APP_PASSWORD).

## Render
Conecte o repositório via render.yaml. Configure APP_PASSWORD e DATABASE_URL (PostgreSQL) no serviço. Nunca publique senhas no GitHub.

## Dados
- `data/veiculos_exportados.json`: classificação original de 30/10/2026, com IDs e placas.
- `data/regioes.csv`: agrupamentos geográficos da logística.
- `revenue.csv`: cabeçalho para importação de itens faturados, sem valores fictícios.

Cada linha em revenue representa um item de faturamento; `item_id` deve ser único. O campo `vehicle_id` prevalece sobre a placa na identificação do tipo. A importação faz upsert. Valores devem usar ponto decimal. Dados financeiros exigem reconciliação com o SISGR antes de uso decisório.

# SisExtractDados_Dintel

Ferramenta **local** que lê arquivos (ou texto colado) e separa, validados e sem
repetição, os **e-mails, telefones, IMEIs, CPFs e nomes próprios** encontrados.
Os resultados aparecem numa tabela no navegador, de onde cada valor (ou um
conjunto) pode ser copiado ou exportado para XLSX/CSV.

- Python 3.8+ **somente com a biblioteca padrão** — nada para instalar além do Python.
- Processamento 100% no computador do usuário (servidor em `127.0.0.1`).
- Cada decisão tem uma **regra com código** (ex.: `R-TEL-03`) e um motivo em português.

---

## 1. Como usar

1. Instale o Python 3.8+ (Windows: marque *Add python.exe to PATH*).
2. Dê **duplo clique** em `_analisador/extrair.bat`. O navegador abre a interface.
3. Arraste arquivos/pastas para a página **ou** cole um texto e clique em *Analisar texto*.
4. Clique num valor para copiar. Para vários: marque as caixas e use *Copiar selecionados*.

Atalho: arrastar arquivos ou pastas **sobre o `extrair.bat`** já abre a interface com eles carregados.

Para encerrar, feche a janela preta ou clique em *Encerrar* na interface.

### Modo sem interface (lote)

```bat
python _analisador\extrair.py --sem-interface pasta_do_caso\ --saida resultado\
```

Gera `extracao_AAAAMMDD_HHMMSS.xlsx` e `.csv`. Opções: `--sem-nomes`, `--porta N`, `--sem-navegador`.

---

## 2. Formatos aceitos

O tipo é identificado pelo **conteúdo** do arquivo, não pela extensão (um `.xls`
que na verdade é HTML — comum em exportações de bancos — é lido corretamente).

| Formato | Observações |
|---|---|
| PDF | texto de todas as páginas, campos de formulário, anotações e metadados; PDFs protegidos só contra cópia/impressão (senha de abertura vazia) são lidos — RC4, AES-128 e AES-256 |
| DOCX, DOC (97-2003), ODT, RTF | corpo, tabelas, cabeçalho/rodapé, notas, comentários e metadados (autor) |
| XLSX, XLS (97-2003), ODS, XML 2003, CSV, TSV | todas as abas, inclusive ocultas; cabeçalho das colunas vira contexto |
| TXT | colunas separadas por TAB viram tabela |
| HTML | texto, tabelas e links `mailto:`/`tel:` |
| JSON | listas de registros viram tabela (chaves = colunas) |
| XML | cada campo vira `tag: valor` |
| EML, MSG (Outlook) | cabeçalhos, corpo e **anexos** (lidos recursivamente) |
| PPTX | slides, tabelas e notas |
| ZIP, GZ | todos os arquivos internos, recursivamente (até 4 níveis) |
| Outros binários | extração de sequências de texto legíveis (como `strings`) |
| Imagens | **sem OCR** — a ferramenta avisa |

---

## 3. Regras de detecção

Princípios comuns:

- **Isolamento:** um número só é extraído se estiver isolado — não pode estar colado em letras
  (ex.: dentro de um ID alfanumérico) nem ser pedaço de uma sequência maior (chave de NF-e,
  linha digitável, ID de transação PIX, nº de processo, cartão, IP).
- **Formatação é evidência:** `(81) 98765-4321` é telefone e `123.456.789-09` é CPF, com ou sem contexto.
- **Contexto:** para números sem formatação vale a **palavra-chave mais próxima antes do valor**,
  na mesma linha (até 40 caracteres, sem atravessar outro número), no fim da linha anterior ou no
  **cabeçalho da coluna** da planilha. Categorias: `cpf`, `imei`, `telefone` e `negativo`
  (conta, agência, protocolo, processo, RG, CNPJ, ID, CEP, valor...). Lista completa em `regras.py`.

### E-mail
| Regra | Descrição | Status |
|---|---|---|
| R-EML-01 | sintaxe válida; domínio em minúsculas | OK (Atenção se domínio temporário, ex. mailinator) |
| R-EML-02 | escrito ofuscado: `fulano [at] gmail [dot] com`, `fulano arroba gmail ponto com` | Atenção |
| R-EML-90/91 | ponto inválido na parte local; termina em extensão de arquivo (`logo@2x.png`) | descartado |

### Telefone
| Regra | Descrição | Status |
|---|---|---|
| R-TEL-01 | com DDD e formatação: `(81) 98765-4321`, `81 9 8765-4321`, `(081) 3333-4444` | OK |
| R-TEL-02 | com código do país: `+55 81 98765-4321`, `5581987654321` (registros do WhatsApp) | OK |
| R-TEL-03 | 11 dígitos sem formatação, DDD válido + celular (9) | OK |
| R-TEL-04 | fixo de 10 dígitos sem formatação — **só com contexto de telefone** (evita timestamps Unix) | OK |
| R-TEL-05 | sem DDD (`98765-4321`); fixo sem DDD só com contexto | Atenção |
| R-TEL-06 | internacional com `+` (país identificado; EUA/Canadá validados pelo padrão NANP) | OK/Atenção |
| R-TEL-07 | 0800/0300/0500/0900 | Atenção |
| R-TEL-9x | DDD inexistente; contexto negativo ("conta 81987654321"); DDI inexistente | descartado |

DDD validado contra os 67 DDDs em uso. Celular de 8 dígitos (formato anterior ao 9º dígito) é aceito com *Atenção*.
Copiar em: `(81) 98765-4321`, `+55 81 98765-4321`, `+5581987654321`, `5581987654321` ou `81987654321`.

### CPF
| Regra | Descrição | Status |
|---|---|---|
| R-CPF-01 | formatado `000.000.000-00` (ou `000000000-00`), DV válido | OK |
| R-CPF-02 | formatado, DV inválido (mantido: pode ser erro de digitação) | Inválido |
| R-CPF-03 | 11 dígitos com contexto "CPF" | OK / Inválido |
| R-CPF-04 | 11 dígitos sem contexto e DV válido | OK |
| R-CPF-05 | 9–10 dígitos em contexto de CPF: **zeros à esquerda restaurados** (CPF que o Excel guardou como número) | Atenção |
| R-CPF-06 | **mascarado** (`***.456.789-**`, comum em comprovantes PIX) — é cruzado com os CPFs completos do lote | Atenção |
| R-CPF-92 | todos os dígitos iguais (`000.000.000-00`) | descartado |

### IMEI
| Regra | Descrição | Status |
|---|---|---|
| R-IMEI-01 | 15 dígitos, Luhn válido e prefixo de órgão emissor (RBI) usual | OK |
| R-IMEI-02 | último dígito **0** no lugar do DV (padrão de extratos de operadora) — DV recalculado | Atenção |
| R-IMEI-03 | contexto "IMEI", mas DV não confere | Inválido |
| R-IMEI-04 | 14 dígitos com contexto "IMEI" (sem DV) — DV calculado | Atenção |
| R-IMEI-05 | IMEISV (16 dígitos) — IMEI derivado | Atenção |
| R-IMEI-06 | Luhn válido, prefixo incomum, com contexto | Atenção |
| R-IMEI-9x | provável IMSI (começa com 724); 15 dígitos sem contexto que falham | descartado |

O mesmo aparelho é consolidado pelos 14 primeiros dígitos (TAC + série): `352099001761481` e
`352099001761480` viram **um** item. O TAC (8 primeiros dígitos) aparece nos detalhes para consulta do modelo.
O prefixo `10` não é aceito sem contexto de propósito: IDs do Facebook têm 15 dígitos e começam com `100`.

### Ambiguidade CPF × celular
Onze dígitos sem formatação podem ser CPF válido **e** celular válido ao mesmo tempo (`R-AMB-01`). Resolução, nesta ordem:
1. `R-AMB-02` — o mesmo número aparece formatado ou com contexto em outro ponto do lote;
2. `R-AMB-03` — na mesma coluna da planilha, ≥ 80% dos valores (mín. 3) são de um tipo;
3. sem evidência: aparece **nas duas listas**, com *Atenção*.

### Nomes próprios (heurística por dicionário — menor precisão que os demais tipos)
| Regra | Descrição | Status |
|---|---|---|
| R-NOM-01 | depois de rótulo (`Nome:`, `Titular:`, `Favorecido -`, `Investigado:`...) ou em coluna com esse cabeçalho | OK |
| R-NOM-02 | começa num **prenome do Censo 2010 (IBGE)** com frequência ≥ 1.000 e tem sobrenome conhecido | OK |
| R-NOM-03 | prenome do Censo + sobrenome fora das listas | Atenção |
| R-NOM-91 | igual ao nome de um município e usado como lugar (`em João Pessoa/PB`) | descartado |
| R-NOM-92 | precedido de logradouro/estabelecimento (`Rua Maria Quitéria`, `Escola José Mariano`) | descartado |

Um nome nunca atravessa quebra de linha, célula, pontuação, número ou palavra da lista de bloqueio
(cargos, instituições, campos de formulário, UFs...). Em texto todo em MAIÚSCULAS, uma palavra final
fora das listas é aceita com *Atenção* (`JOÃO SILVA ESTELIONATARIO`); duas ou mais são cortadas.
Enumeração com "e" seguida de outro prenome separa as pessoas (`Maria e José Ferreira`).
Nomes que coincidem com município são sinalizados (`Maria Helena/PR`). A interface indica quando um
nome está contido em outro maior (`João Silva` ⊂ `João Silva Santos`).

---

## 4. Status, descartes e rastreabilidade

- **OK** (verde): validado pela regra. **Atenção** (amarelo): precisa de conferência humana.
  **Inválido** (vermelho): mantido porque o contexto indica o tipo, mas a validação falhou.
- **Descartados:** aba própria com tudo que pareceu um dos tipos mas foi rejeitado, com motivo e trecho.
- Cada item mostra **onde aparece** (arquivo, página/aba/linha/coluna e trecho com destaque) — até 30 ocorrências.
- Cada arquivo carregado mostra o **SHA-256** do conteúdo recebido (aba *Fontes* do XLSX).

## 5. Copiar e exportar

- Clique num valor: copia no formato escolhido em *Formato de cópia* (telefone, CPF, IMEI, nome, e-mail).
- *Copiar selecionados* / *Copiar lista*: um valor por linha.
- *Copiar tabela*: cola no Excel/Word como tabela, **mantendo CPF e IMEI como texto** (sem perder o zero à
  esquerda nem virar notação científica).
- *Colunas por tipo*: uma coluna para cada tipo, com botão para copiar a coluna inteira.
- **XLSX**: abas *Dados*, uma por tipo, *Ocorrências*, *Descartados* e *Fontes* (com SHA-256); valores
  gravados como texto e status colorido. **CSV**: `;`, UTF-8 com BOM, proteção contra fórmula.
- As ações valem para os itens **selecionados**; sem seleção, para a lista filtrada.

## 6. Segurança e sigilo

- O servidor escuta **só em 127.0.0.1**: nenhuma outra máquina da rede acessa.
- Cada execução gera um **token aleatório** exigido em todas as chamadas, e o cabeçalho `Host` é conferido
  (impede que um site aberto no navegador use o servidor local; proteção contra DNS rebinding).
- A página não carrega nada da internet (sem CDN, sem fontes externas; CSP restritiva).
- **Nada é gravado em disco**: arquivos e resultados ficam na memória e somem ao encerrar.
  Exportações só são salvas quando o usuário clica em XLSX/CSV.
- `.gitignore` bloqueia planilhas, PDFs e documentos fora de `tests/amostras/` (amostras **sintéticas**).

## 7. Limitações conhecidas

1. **Sem OCR**: PDF digitalizado e imagens não têm texto. A ferramenta lista as páginas sem camada de texto.
2. **Células estreitas em PDF**: quando o próprio PDF quebra o conteúdo de uma célula em duas linhas
   (`pedro.alves@exe` / `mplo.org`), o dado sai partido — o mesmo ocorre com o `pdftotext`. Prefira a
   planilha original ou cole o texto.
3. **PDF com senha de abertura**, **XLS/DOC criptografados**, **7z/RAR**: não lidos (há aviso).
4. **Nomes**: heurística. Prenomes raros (fora dos ~8 mil do Censo com frequência ≥ 1.000) só são achados
   pela regra de rótulo/coluna; podem ocorrer falsos positivos em títulos e nomes de lugares.
5. **Contexto** só olha para trás (`CPF 123...`), não para frente (`123... (CPF)`).
6. `.msg` (Outlook) foi implementado pela especificação, mas não testado com mensagens reais.
7. Desempenho de referência: planilha CSV de 200 mil linhas (17 MB) em ~8 s; texto corrido muito denso
   (8 MB, um telefone/e-mail/CPF/nome por linha) em ~18 s.

---

## 8. Estrutura do código

```
_analisador/
├── extrair.bat           duplo clique / arrastar arquivos (Windows)
├── extrair.py            ponto de entrada (verifica Python ≥ 3.8)
└── extdados/
    ├── leitores.py       arquivo -> blocos de texto/tabela (TXT, CSV, HTML, JSON, XML, EML, ZIP, RTF, strings)
    ├── office.py         DOCX, XLSX, PPTX, ODT, ODS, ODP
    ├── ole.py            OLE2: XLS (BIFF5/8), DOC (tabela de peças), MSG
    ├── pdf.py            PDF: objetos, object streams, filtros, fontes/ToUnicode, formulários
    ├── cripto.py         RC4, AES-128/256 (tabelas T) e manipulador de segurança do PDF
    ├── normaliza.py      limpeza de texto (NFKC, invisíveis, traços/espaços tipográficos)
    ├── regras.py         DDDs, DDIs, RBIs, palavras de contexto, rótulos de nome
    ├── detectores.py     e-mail, telefone, IMEI, CPF (cadeias numéricas + modelos)
    ├── nomes.py          nomes próprios (rótulo + dicionário)
    ├── analise.py        unidades -> candidatos -> resolução de ambiguidade -> itens consolidados
    ├── exportar.py       XLSX (escritor próprio) e CSV
    ├── servidor.py       HTTP local com token
    ├── cli.py            linha de comando
    ├── interface/        index.html (página única, sem dependências)
    └── dados/            prenomes (IBGE), sobrenomes, palavras de bloqueio, municípios
ferramentas/preparar_dados.py   regenera dados/ a partir das fontes públicas
tests/                           77 testes (unittest) + amostras sintéticas + gerador
```

### Testes

```bat
python -m unittest discover -s tests -v
```

Validados em Python 3.8, 3.11, 3.12 e 3.13. Cobrem cada regra de detecção, os formatos de arquivo
contra um gabarito (`tests/amostras/gabarito.json`), PDFs criptografados (RC4/AES-128/AES-256, com e sem
senha de abertura), AES contra os vetores do FIPS-197, exportação XLSX/CSV e a segurança do servidor
(token, Host). `tests/gerar_amostras.py` recria as amostras (exige python-docx, openpyxl, reportlab,
python-pptx, LibreOffice e qpdf — só em desenvolvimento).

## 9. Fontes de dados

- **Prenomes**: IBGE, Censo 2010 — *Nomes no Brasil* (10 mil mais frequentes por sexo), compilados em
  [MedidaSP/nomes-brasileiros-ibge](https://github.com/MedidaSP/nomes-brasileiros-ibge).
- **Municípios** (nome, UF, DDD): [kelvins/municipios-brasileiros](https://github.com/kelvins/municipios-brasileiros) (MIT).
- **Sobrenomes** e **palavras de bloqueio**: listas curadas, editáveis em `extdados/dados/`.

## 10. Próximos passos possíveis

- Outros identificadores: CNPJ, chave PIX aleatória, placa, RG, IP, nº de processo CNJ.
- Leitura de contexto **depois** do valor.
- OCR opcional (Tesseract local) para PDFs digitalizados.

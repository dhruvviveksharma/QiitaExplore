# Memory

## Me
Dhruv (dhruvviveksharma@gmail.com)

## Projects
| Name | What |
|------|------|
| **qiita-web** | Local Qiita microbiome data analysis frontend. Studies stored in `test_data_studies/studies/{id}/` with sample_template, prep_template, and otu_table.biom |

## Import Workflow
Each study directory needs:
- `sample_template_{id}.txt` — sample metadata (downloaded from Qiita study page)
- `prep_template_{id}.txt` — prep metadata (from Qiita prep info page)
- `otu_table.biom` — BIOM artifact (from Qiita artifact download)

Load via `commands.sh` which calls: `qiita db load-study`, `qiita db load-sample-template`, `qiita db load-prep-template`, `qiita db load-artifact`

## important points
Whenever we are interacting with the chatbot, I must see status of what function/ tool is being used. I need this information as the user so I know something is working in the background.

When we are testing, I wish to use port 5002 to tunnel into my local website and on barnacle. When I merge into master, I want the default ports to change to 5001 again.

No code file under ezredbiom should be over 500 lines of code. Any future changes you wish to make, you can add it to "~/qiita-web/TICKETS/tickets.md"
import click
import json
from pathlib import Path

from extractor.core import extract_csa
import asyncio
from extractor.parsers.csa_llm_extraction import extract_csa as llm_extract_csa, extract_csa_fields as llm_extract_csa_fields


@click.command()
@click.argument("file_path")
@click.option("--output", "-o", help="Output JSON file")
@click.option("--with-llm/--no-llm", default=False, help="Also run LLM extractor and add under 'llm' key")
@click.option("--llm-fields", default="", help="Comma-separated subset of fields to extract with LLM (optional)")
def cli(file_path, output, with_llm, llm_fields):
    result = extract_csa(file_path)
    # Optionally run LLM extractor when API key is configured
    llm = None
    if with_llm:
        html_bytes = Path(file_path).read_bytes()
        try:
            html_str = html_bytes.decode("utf-8", errors="ignore")
            if llm_fields.strip():
                fields = [f.strip() for f in llm_fields.split(",") if f.strip()]
                llm_obj = asyncio.run(llm_extract_csa_fields(html_str, fields))
            else:
                llm_obj = asyncio.run(llm_extract_csa(html_str))
            llm = llm_obj.dict()
        except Exception as e:
            llm = {"error": str(e)}

    final = result
    if llm is not None:
        # Attach LLM results under 'llm'
        final = {**result, "llm": llm}

    json_str = json.dumps(final, indent=2, ensure_ascii=False)
    if output:
        Path(output).write_text(json_str)
        click.echo(f"Saved to {output}")
    else:
        click.echo(json_str)


if __name__ == "__main__":
    cli()

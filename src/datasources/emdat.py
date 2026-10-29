from typing import List

import duckdb
import ocha_stratus as stratus


def load_emat(iso3s: List[str] = None):
    blob_name = "emdat/processed/emdat_all.parquet"
    url = (
        stratus.get_container_client(container_name="global")
        .get_blob_client(blob_name)
        .url
    )
    query = """
    SELECT *
    FROM read_parquet('{url}')
    WHERE "Disaster Subtype" = 'Tropical cyclone' AND Historic = 'No'
    """
    if iso3s is not None and len(iso3s) > 0:
        iso3s_upper = [iso3.upper() for iso3 in iso3s]
        iso3_list = ", ".join([f"'{iso3}'" for iso3 in iso3s_upper])
        query += f" AND ISO IN ({iso3_list})"
    con = duckdb.connect()
    df_emdat = con.execute(query.format(url=url)).df()
    return df_emdat

def main():
    snakemake = mock_snakemake("build_bus_regions")
    print(snakemake["rule"])

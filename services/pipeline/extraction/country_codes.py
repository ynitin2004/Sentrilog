"""Issuing-state and nationality codes a TD3 MRZ may carry (ICAO Doc 9303 Part 3, section 5):
the ISO 3166-1 alpha-3 codes plus ICAO's own additions. Used as a plausibility check on line 1,
which has no check digit of its own (see mrz.parse_td3).

A code missing from here makes a genuine passport fall back to the VLM path -- slower, not
wrong -- so the list errs towards inclusion, keeping withdrawn codes that older passports may
still show.
"""

ISO_3166_ALPHA3 = frozenset("""
    ABW AFG AGO AIA ALA ALB AND ARE ARG ARM ASM ATA ATF ATG AUS AUT AZE BDI BEL BEN BES BFA BGD
    BGR BHR BHS BIH BLM BLR BLZ BMU BOL BRA BRB BRN BTN BVT BWA CAF CAN CCK CHE CHL CHN CIV CMR
    COD COG COK COL COM CPV CRI CUB CUW CXR CYM CYP CZE DEU DJI DMA DNK DOM DZA ECU EGY ERI ESH
    ESP EST ETH FIN FJI FLK FRA FRO FSM GAB GBR GEO GGY GHA GIB GIN GLP GMB GNB GNQ GRC GRD GRL
    GTM GUF GUM GUY HKG HMD HND HRV HTI HUN IDN IMN IND IOT IRL IRN IRQ ISL ISR ITA JAM JEY JOR
    JPN KAZ KEN KGZ KHM KIR KNA KOR KWT LAO LBN LBR LBY LCA LIE LKA LSO LTU LUX LVA MAC MAF MAR
    MCO MDA MDG MDV MEX MHL MKD MLI MLT MMR MNE MNG MNP MOZ MRT MSR MTQ MUS MWI MYS MYT NAM NCL
    NER NFK NGA NIC NIU NLD NOR NPL NRU NZL OMN PAK PAN PCN PER PHL PLW PNG POL PRI PRK PRT PRY
    PSE PYF QAT REU ROU RUS RWA SAU SDN SEN SGP SGS SHN SJM SLB SLE SLV SMR SOM SPM SRB SSD STP
    SUR SVK SVN SWE SWZ SXM SYC SYR TCA TCD TGO THA TJK TKL TKM TLS TON TTO TUN TUR TUV TWN TZA
    UGA UKR UMI URY USA UZB VAT VCT VEN VGB VIR VNM VUT WLF WSM YEM ZAF ZMB ZWE
    ANT YUG ZAR TMP SCG
    """.split())

# ICAO additions: Germany's single-letter code, British nationality categories, UN
# organisations, the stateless and refugee categories, and others assigned by ICAO. UTO --
# ICAO's fictional "Utopia", printed on specimen documents -- is deliberately absent: a
# specimen must never be trusted as a real passport.
ICAO_ADDITIONAL = frozenset("""
    D<< GBD GBN GBO GBP GBS UNO UNA UNK XXA XXB XXC XXX EUE XOM XPO XCC XCE XES XIM XEC XBA
    RKS
    """.split())

TD3_STATE_CODES = ISO_3166_ALPHA3 | ICAO_ADDITIONAL

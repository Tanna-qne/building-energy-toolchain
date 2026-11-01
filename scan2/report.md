# 措施組合與回收期

- 貨幣 USD; 電價: 0.13/kWh; 天然氣: 0.04/kWh(costs.json)
- 貼膜: 40/m² 玻璃; 照明: 20/m² 樓地板(有照明的 Zone); 屋頂反射漆: 12/m² 屋頂
- 年省 % 以總能源(電 + 天然氣)計

| 模型 | 情境 | 用電 kWh | 天然氣 kWh | EUI kWh/m² | 年省 % | 年省 USD | 投資 USD | 回收年 | 備註 |
|---|---|---|---|---|---|---|---|---|---|
| 5ZoneAirCooled | baseline | 43510.1 | 19018.4 | 134.9 | 0.0 |  |  |  |  |
| 5ZoneAirCooled | lights_-30% | 36147.3 | 20139.2 | 121.4 | 10.0 | 912 | 9272 | 10.2 |  |
| 5ZoneAirCooled | film_shgc0.30 | 42269.7 | 20575.8 | 135.6 | -0.5 | 99 | 2436 | 24.6 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooled | lights+film | 34938.8 | 20917.9 | 120.5 | 10.7 | 1038 | 11708 | 11.3 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooled | infil_x0.5 | 43521.0 | 18051.8 | 132.8 | 1.5 | 37 |  |  |  |
| 5ZoneAirCooled | coolroof | 43157.0 | 19226.5 | 134.6 | 0.2 | 38 | 5563 | 148.0 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooled | equip_-20% | 40417.3 | 19671.9 | 129.6 | 3.9 | 376 |  |  |  |
| 5ZoneAirCooled | insulation | 42856.5 | 17274.2 | 129.7 | 3.8 | 155 | 33201 | 214.6 | 外牆 U 0.38 → 0.25、屋頂 U 0.27 → 0.18 W/m²K(在原構造加一層保溫) |
| 5ZoneAirCooled | hvac_eff | 42597.3 | 16015.5 | 126.4 | 6.3 | 239 | 41724 | 174.7 | Electric Nominal COP 3.2 → 4、Hotwater Nominal Thermal Efficiency 0.8 → 0.95 |
| 5ZoneAirCooledConvCoef | baseline | 43822.4 | 20328.6 | 138.4 | 0.0 |  |  |  |  |
| 5ZoneAirCooledConvCoef | lights_-30% | 36567.0 | 20926.6 | 124.0 | 10.4 | 919 | 9272 | 10.1 |  |
| 5ZoneAirCooledConvCoef | film_shgc0.30 | 42945.6 | 21345.0 | 138.7 | -0.2 | 73 | 2436 | 33.2 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledConvCoef | lights+film | 35742.7 | 21938.8 | 124.4 | 10.1 | 986 | 11708 | 11.9 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledConvCoef | infil_x0.5 | 43822.3 | 19986.0 | 137.6 | 0.5 | 14 |  |  |  |
| 5ZoneAirCooledConvCoef | coolroof | 43391.0 | 20658.9 | 138.2 | 0.2 | 43 | 5563 | 129.8 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooledConvCoef | equip_-20% | 40769.3 | 20786.4 | 132.8 | 4.0 | 379 |  |  |  |
| 5ZoneAirCooledConvCoef | insulation | 43121.8 | 19093.4 | 134.2 | 3.0 | 140 | 33201 | 236.3 | 外牆 U 0.38 → 0.25、屋頂 U 0.27 → 0.18 W/m²K(在原構造加一層保溫) |
| 5ZoneAirCooledConvCoef | hvac_eff | 42965.0 | 17143.9 | 129.7 | 6.3 | 239 | 41724 | 174.7 | Electric Nominal COP 3.2 → 4、Hotwater Nominal Thermal Efficiency 0.8 → 0.95、Constantvolume Fan Total Efficiency 0.5 → 0.7 |
| 5ZoneAirCooledDemandLimiting | baseline | 39108.0 | 19199.7 | 125.8 | 0.0 |  |  |  |  |
| 5ZoneAirCooledDemandLimiting | lights_-30% | 38259.4 | 18408.2 | 122.2 | 2.8 | 142 | 9272 | 65.3 |  |
| 5ZoneAirCooledDemandLimiting | film_shgc0.30 | 38724.5 | 18051.1 | 122.5 | 2.6 | 96 | 2436 | 25.4 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledDemandLimiting | lights+film | 38134.6 | 17004.5 | 118.9 | 5.4 | 214 | 11708 | 54.6 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledDemandLimiting | infil_x0.5 | 39107.7 | 18930.2 | 125.2 | 0.5 | 11 |  |  |  |
| 5ZoneAirCooledDemandLimiting | coolroof | 38933.0 | 18778.1 | 124.5 | 1.0 | 40 | 5563 | 140.4 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooledDemandLimiting | equip_-20% | 38937.9 | 18703.9 | 124.3 | 1.1 | 42 |  |  |  |
| 5ZoneAirCooledDemandLimiting | insulation | 38714.0 | 16719.5 | 119.6 | 4.9 | 150 | 33201 | 220.7 | 外牆 U 0.38 → 0.25、屋頂 U 0.27 → 0.18 W/m²K(在原構造加一層保溫) |
| 5ZoneAirCooledDemandLimiting | hvac_eff | 38890.5 | 16167.5 | 118.8 | 5.6 | 150 | 41724 | 279.0 | Electric Nominal COP 3.2 → 4、Hotwater Nominal Thermal Efficiency 0.8 → 0.95 |
| HVACTemplate-5ZoneVAVFanPowered | baseline | 55254.9 | 14927.3 | 151.4 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | lights_-30% | 47237.2 | 15585.0 | 135.5 | 10.5 | 1016 | 9272 | 9.1 |  |
| HVACTemplate-5ZoneVAVFanPowered | film_shgc0.30 | 52527.5 | 16496.0 | 148.9 | 1.7 | 292 | 2436 | 8.3 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneVAVFanPowered | lights+film | 44541.9 | 17330.2 | 133.5 | 11.8 | 1297 | 11708 | 9.0 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneVAVFanPowered | infil_x0.5 | 55155.6 | 14909.4 | 151.1 | 0.2 | 14 |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | coolroof | 54462.1 | 15038.5 | 149.9 | 1.0 | 99 | 5563 | 56.4 | 屋頂日射吸收率 0.65 → 0.30 |
| HVACTemplate-5ZoneVAVFanPowered | equip_-20% | 52271.0 | 15309.7 | 145.8 | 3.7 | 373 |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | insulation | 53703.8 | 13934.9 | 145.9 | 3.6 | 241 | 33201 | 137.6 | 外牆 U 0.38 → 0.25、屋頂 U 0.27 → 0.18 W/m²K(在原構造加一層保溫) |
| HVACTemplate-5ZoneVAVFanPowered | hvac_eff | 53052.4 | 12570.4 | 141.6 | 6.5 | 381 | 41724 | 109.6 | Chiller Nominal COP 3.2 → 4、Boiler Efficiency 0.8 → 0.95 |
| HVACTemplate-5ZoneBaseboardHeat | baseline | 35864.7 | 1109.6 | 79.8 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | lights_-30% | 28761.6 | 1226.8 | 64.7 | 18.9 | 919 | 9272 | 10.1 |  |
| HVACTemplate-5ZoneBaseboardHeat | film_shgc0.30 | 35758.8 | 1304.4 | 79.9 | -0.2 | 6 | 2436 | 407.7 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneBaseboardHeat | lights+film | 28647.3 | 1452.7 | 64.9 | 18.6 | 925 | 11708 | 12.7 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneBaseboardHeat | infil_x0.5 | 35867.9 | 1003.2 | 79.5 | 0.3 | 4 |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | coolroof | 35817.1 | 1137.5 | 79.7 | 0.1 | 5 | 5563 | 1096.8 | 屋頂日射吸收率 0.65 → 0.30 |
| HVACTemplate-5ZoneBaseboardHeat | equip_-20% | 33544.0 | 1149.8 | 74.8 | 6.2 | 300 |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | insulation | 35819.7 | 739.3 | 78.9 | 1.1 | 21 | 33201 | 1606.9 | 外牆 U 0.38 → 0.25、屋頂 U 0.27 → 0.18 W/m²K(在原構造加一層保溫) |
| HVACTemplate-5ZoneBaseboardHeat | hvac_eff | 35864.7 | 934.4 | 79.4 | 0.5 | 7 | 41724 | 5953.8 | Boiler Efficiency 0.8 → 0.95 |

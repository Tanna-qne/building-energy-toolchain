# 措施組合與回收期

- 電價假設: 4.5 TWD/kWh; 天然氣: 1.5 TWD/kWh
- 貼膜: 1200 TWD/m² 玻璃; 照明: 600 TWD/m² 樓地板(有照明的 Zone); 屋頂反射漆: 350 TWD/m² 屋頂
- 年省 % 以總能源(電 + 天然氣)計

| 模型 | 情境 | 用電 kWh | 天然氣 kWh | EUI kWh/m² | 年省 % | 年省 TWD | 投資 TWD | 回收年 | 備註 |
|---|---|---|---|---|---|---|---|---|---|
| 5ZoneAirCooled | baseline | 43510.1 | 19018.4 | 134.9 | 0.0 |  |  |  |  |
| 5ZoneAirCooled | lights_-30% | 36147.3 | 20139.2 | 121.4 | 10.0 | 31451 | 278160 | 8.8 |  |
| 5ZoneAirCooled | film_shgc0.30 | 42269.7 | 20575.8 | 135.6 | -0.5 | 3246 | 73080 | 22.5 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooled | lights+film | 34938.8 | 20917.9 | 120.5 | 10.7 | 35722 | 351240 | 9.8 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooled | infil_x0.5 | 43521.0 | 18051.8 | 132.8 | 1.5 | 1401 |  |  |  |
| 5ZoneAirCooled | coolroof | 43157.0 | 19226.5 | 134.6 | 0.2 | 1277 | 162260 | 127.1 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooled | equip_-20% | 40417.3 | 19671.9 | 129.6 | 3.9 | 12937 |  |  |  |
| 5ZoneAirCooledConvCoef | baseline | 43822.4 | 20328.6 | 138.4 | 0.0 |  |  |  |  |
| 5ZoneAirCooledConvCoef | lights_-30% | 36567.0 | 20926.6 | 124.0 | 10.4 | 31752 | 278160 | 8.8 |  |
| 5ZoneAirCooledConvCoef | film_shgc0.30 | 42945.6 | 21345.0 | 138.7 | -0.2 | 2421 | 73080 | 30.2 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledConvCoef | lights+film | 35742.7 | 21938.8 | 124.4 | 10.1 | 33943 | 351240 | 10.3 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledConvCoef | infil_x0.5 | 43822.3 | 19986.0 | 137.6 | 0.5 | 514 |  |  |  |
| 5ZoneAirCooledConvCoef | coolroof | 43391.0 | 20658.9 | 138.2 | 0.2 | 1446 | 162260 | 112.2 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooledConvCoef | equip_-20% | 40769.3 | 20786.4 | 132.8 | 4.0 | 13052 |  |  |  |
| 5ZoneAirCooledDemandLimiting | baseline | 39108.0 | 19199.7 | 125.8 | 0.0 |  |  |  |  |
| 5ZoneAirCooledDemandLimiting | lights_-30% | 38259.4 | 18408.2 | 122.2 | 2.8 | 5006 | 278160 | 55.6 |  |
| 5ZoneAirCooledDemandLimiting | film_shgc0.30 | 38724.5 | 18051.1 | 122.5 | 2.6 | 3449 | 73080 | 21.2 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledDemandLimiting | lights+film | 38134.6 | 17004.5 | 118.9 | 5.4 | 7673 | 351240 | 45.8 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| 5ZoneAirCooledDemandLimiting | infil_x0.5 | 39107.7 | 18930.2 | 125.2 | 0.5 | 406 |  |  |  |
| 5ZoneAirCooledDemandLimiting | coolroof | 38933.0 | 18778.1 | 124.5 | 1.0 | 1420 | 162260 | 114.3 | 屋頂日射吸收率 0.65 → 0.30 |
| 5ZoneAirCooledDemandLimiting | equip_-20% | 38937.9 | 18703.9 | 124.3 | 1.1 | 1509 |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | baseline | 55254.9 | 14927.3 | 151.4 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | lights_-30% | 47237.2 | 15585.0 | 135.5 | 10.5 | 35093 | 278160 | 7.9 |  |
| HVACTemplate-5ZoneVAVFanPowered | film_shgc0.30 | 52527.5 | 16496.0 | 148.9 | 1.7 | 9920 | 73080 | 7.4 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneVAVFanPowered | lights+film | 44541.9 | 17330.2 | 133.5 | 11.8 | 44604 | 351240 | 7.9 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneVAVFanPowered | infil_x0.5 | 55155.6 | 14909.4 | 151.1 | 0.2 | 474 |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | coolroof | 54462.1 | 15038.5 | 149.9 | 1.0 | 3401 | 162260 | 47.7 | 屋頂日射吸收率 0.65 → 0.30 |
| HVACTemplate-5ZoneVAVFanPowered | equip_-20% | 52271.0 | 15309.7 | 145.8 | 3.7 | 12854 |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | baseline | 35864.7 | 1109.6 | 79.8 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | lights_-30% | 28761.6 | 1226.8 | 64.7 | 18.9 | 31788 | 278160 | 8.8 |  |
| HVACTemplate-5ZoneBaseboardHeat | film_shgc0.30 | 35758.8 | 1304.4 | 79.9 | -0.2 | 184 | 73080 | 396.4 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneBaseboardHeat | lights+film | 28647.3 | 1452.7 | 64.9 | 18.6 | 31964 | 351240 | 11.0 | 原窗 SHGC 0.76 → 貼膜後 0.30(U 值維持 3.22) |
| HVACTemplate-5ZoneBaseboardHeat | infil_x0.5 | 35867.9 | 1003.2 | 79.5 | 0.3 | 145 |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | coolroof | 35817.1 | 1137.5 | 79.7 | 0.1 | 172 | 162260 | 941.5 | 屋頂日射吸收率 0.65 → 0.30 |
| HVACTemplate-5ZoneBaseboardHeat | equip_-20% | 33544.0 | 1149.8 | 74.8 | 6.2 | 10383 |  |  |  |

# 措施組合與回收期

- 電價假設: 4.5 TWD/kWh; 天然氣: 1.5 TWD/kWh
- 貼膜: 1200 TWD/m² 玻璃; 照明: 600 TWD/m² 樓地板(有照明的 Zone)
- 年省 % 以總能源(電 + 天然氣)計

| 模型 | 情境 | 用電 kWh | 天然氣 kWh | EUI kWh/m² | 年省 % | 年省 TWD | 投資 TWD | 回收年 | 備註 |
|---|---|---|---|---|---|---|---|---|---|
| 5ZoneAirCooled | baseline | 43510.1 | 19018.4 | 134.9 | 0.0 |  |  |  |  |
| 5ZoneAirCooled | lights_-30% | 36147.3 | 20139.2 | 121.4 | 10.0 | 31451 | 278160 | 8.8 |  |
| 5ZoneAirCooled | film_shgc0.30 | 42262.5 | 20134.1 | 134.6 | 0.2 | 3941 | 73080 | 18.5 |  |
| 5ZoneAirCooled | lights+film | 34928.1 | 20455.1 | 119.5 | 11.4 | 36464 | 351240 | 9.6 |  |
| 5ZoneAirCooled | infil_x0.5 | 43521.0 | 18051.8 | 132.8 | 1.5 | 1401 |  |  |  |
| 5ZoneAirCooledConvCoef | baseline | 43822.4 | 20328.6 | 138.4 | 0.0 |  |  |  |  |
| 5ZoneAirCooledConvCoef | lights_-30% | 36567.0 | 20926.6 | 124.0 | 10.4 | 31752 | 278160 | 8.8 |  |
| 5ZoneAirCooledConvCoef | film_shgc0.30 | 42935.6 | 21187.6 | 138.3 | 0.0 | 2702 | 73080 | 27.0 |  |
| 5ZoneAirCooledConvCoef | lights+film | 35732.3 | 21776.1 | 124.0 | 10.4 | 34234 | 351240 | 10.3 |  |
| 5ZoneAirCooledConvCoef | infil_x0.5 | 43822.3 | 19986.0 | 137.6 | 0.5 | 514 |  |  |  |
| 5ZoneAirCooledDemandLimiting | baseline | 39108.0 | 19199.7 | 125.8 | 0.0 |  |  |  |  |
| 5ZoneAirCooledDemandLimiting | lights_-30% | 38259.4 | 18408.2 | 122.2 | 2.8 | 5006 | 278160 | 55.6 |  |
| 5ZoneAirCooledDemandLimiting | film_shgc0.30 | 38730.9 | 17754.5 | 121.8 | 3.1 | 3865 | 73080 | 18.9 |  |
| 5ZoneAirCooledDemandLimiting | lights+film | 38132.0 | 16706.4 | 118.3 | 5.9 | 8132 | 351240 | 43.2 |  |
| 5ZoneAirCooledDemandLimiting | infil_x0.5 | 39107.7 | 18930.2 | 125.2 | 0.5 | 406 |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | baseline | 55254.9 | 14927.3 | 151.4 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneVAVFanPowered | lights_-30% | 47237.2 | 15585.0 | 135.5 | 10.5 | 35093 | 278160 | 7.9 |  |
| HVACTemplate-5ZoneVAVFanPowered | film_shgc0.30 | 52497.1 | 16286.6 | 148.4 | 2.0 | 10371 | 73080 | 7.0 |  |
| HVACTemplate-5ZoneVAVFanPowered | lights+film | 44508.8 | 17111.8 | 132.9 | 12.2 | 45081 | 351240 | 7.8 |  |
| HVACTemplate-5ZoneVAVFanPowered | infil_x0.5 | 55155.6 | 14909.4 | 151.1 | 0.2 | 474 |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | baseline | 35864.7 | 1109.6 | 79.8 | 0.0 |  |  |  |  |
| HVACTemplate-5ZoneBaseboardHeat | lights_-30% | 28761.6 | 1226.8 | 64.7 | 18.9 | 31788 | 278160 | 8.8 |  |
| HVACTemplate-5ZoneBaseboardHeat | film_shgc0.30 | 35759.0 | 1238.9 | 79.8 | -0.1 | 282 | 73080 | 259.4 |  |
| HVACTemplate-5ZoneBaseboardHeat | lights+film | 28647.7 | 1378.8 | 64.8 | 18.8 | 32073 | 351240 | 11.0 |  |
| HVACTemplate-5ZoneBaseboardHeat | infil_x0.5 | 35867.9 | 1003.2 | 79.5 | 0.3 | 145 |  |  |  |

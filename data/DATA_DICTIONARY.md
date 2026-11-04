# Shared Bicycle Data — Repository README & Data Dictionary

## 1. Folder Structure
```
Shared bicycle data/
├─ Algorithm data/               # MATLAB scripts for clustering & prediction
├─ Data cleaning algorithm/      # Python scripts for preprocessing
├─ Electronic fence data/        # Fence IDs, names, and four-corner coordinates
├─ Order data/                   # Trip orders and vehicle state logs
├─ Trajectory data/              # GPS traces by bicycle and time
└─ Weather data/                 # Hourly weather observations
```

## 2. File Naming Conventions
- `order data.csv` — Orders of the specified day  
- `trajectory data_YYYYMMDD.csv` — Trajectories of the specified day  
- `Electronic fence_data.csv` — Electronic fence definitions  
- `weather data.csv` — Monthly meteorological data  

All files use **UTF-8 encoding** and **comma separators**.

---

## 3. Metadata and Units
| Category | Specification |
|-----------|---------------|
| Time zone | China Standard Time (UTC+8) |
| Coordinate system | WGS-84 |
| Distance | meters (m) |
| Duration | seconds (s) |
| Temperature | Celsius (°C) |
| Wind speed | meters per second (m/s) |
| Precipitation/Snow | millimeters (mm) |
| Pressure | hPa |
| Encoding | UTF-8 |

---

## 4. Variable Definitions

### (a) Electronic fence data
| Variable | Type | Unit | Description |
|-----------|------|------|-------------|
| FENCE_ID | string | — | Unique fence identifier |
| FENCE_NAME | string | — | Fence name |
| LAT1–LAT4, LNG1–LNG4 | float | ° | Coordinates of the four fence corners |

### (b) Order data
| Variable | Type | Unit | Description |
|-----------|------|------|-------------|
| BICYCLE_ID | string | — | Bicycle ID |
| LATITUDE | float | ° | Current latitude |
| LONGITUDE | float | ° | Current longitude |
| LOCK_STATUS | integer | 0/1 | Lock status (0 = unlocked, 1 = locked) |
| UPDATE_TIME1 | datetime | UTC+8 | Start or first update time |
| UPDATE_TIME2 | datetime | UTC+8 | End or last update time |
| ORDER_DURATION | float | s | Order duration |

### (c) Trajectory data
| Variable | Type | Unit | Description |
|-----------|------|------|-------------|
| BICYCLE_ID | string | — | Bicycle ID |
| LOCATING_TIME | datetime | UTC+8 | GPS locating time |
| LATITUDE | float | ° | Latitude |
| LONGITUDE | float | ° | Longitude |

### (d) Weather data
| Variable | Type | Unit | Description |
|-----------|------|------|-------------|
| TIME | datetime | UTC+8 | Observation time |
| TEMP | float | °C | Air temperature |
| DWPT | float | °C | Dew point |
| RHUM | float | % | Relative humidity |
| PRCP | float | mm | Precipitation |
| SNOW | float | mm | Snowfall |
| WDIR | float | ° | Wind direction |
| WSPD | float | m/s | Wind speed |
| WPGT | float | m/s | Wind gust |
| PRES | float | hPa | Pressure |
| TSUN | float | h | Sunshine duration |
| COCO | int | — | Weather condition code |

---

## 5. Script Descriptions
- **Algorithm data/** — MATLAB clustering & regression models.  
- **Data cleaning algorithm/** — Python scripts for cleaning, trajectory denoising.  
Each script includes inline comments and can run independently.


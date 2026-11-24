/* ============================================================
   i18n.js — Chinese -> English display dictionary
   The backend stores Chinese weather labels (weather_type) as-is
   in MySQL, so a lookup table is needed to render them in English.
   ============================================================ */
const I18N = (function () {

    /** weather_type (zh) -> { en, icon } */
    const WEATHER = {
        '晴':    { en: 'Clear',            icon: '☀️' },
        '少云':  { en: 'Partly Cloudy',    icon: '🌤' },
        '多云':  { en: 'Cloudy',           icon: '⛅' },
        '阴':    { en: 'Overcast',         icon: '☁️' },
        '雾':    { en: 'Fog',              icon: '🌫' },
        '冻雾':  { en: 'Freezing Fog',     icon: '🌫' },
        '小雨':  { en: 'Light Rain',       icon: '🌦' },
        '中雨':  { en: 'Moderate Rain',    icon: '🌧' },
        '大雨':  { en: 'Heavy Rain',       icon: '⛈' },
        '暴雨':  { en: 'Torrential Rain',  icon: '⛈' },
        '阵雨':  { en: 'Showers',          icon: '🌦' },
        '雷阵雨': { en: 'Thunderstorms',   icon: '⛈' },
        '雨夹雪': { en: 'Sleet',           icon: '🌨' },
        '小雪':  { en: 'Light Snow',       icon: '🌨' },
        '中雪':  { en: 'Moderate Snow',    icon: '❄️' },
        '大雪':  { en: 'Heavy Snow',       icon: '❄️' }
    };

    const FALLBACK = { en: 'Unknown', icon: '☁️' };

    /** Translate a Chinese weather label to English. */
    function weather(zh) {
        if (!zh) return FALLBACK.en;
        return (WEATHER[zh] || { en: zh }).en;
    }

    /** Emoji for a Chinese weather label. */
    function weatherIcon(zh) {
        if (!zh) return FALLBACK.icon;
        return (WEATHER[zh] || FALLBACK).icon;
    }

    return { weather, weatherIcon, WEATHER };
})();

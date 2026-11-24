/* ============================================================
   chart.js — ECharts time-series lines + weather comparison bars
   ============================================================ */
const BikeChart = (function () {
    let tideChart = null;
    let weatherChart = null;
    let seriesData = [];

    // Legend names and series names must match exactly, otherwise ECharts drops the legend entry.
    const S_BORROW = 'Borrows';
    const S_RETURN = 'Returns';
    const S_NET = 'Net Flow';
    const S_ETI = 'Tide Index E(t)';
    const W_BORROW = 'Total Borrows';
    const W_RETURN = 'Total Returns';

    const AXIS_COLOR = '#8ea3c8';
    const SPLIT_COLOR = 'rgba(88,148,255,.12)';

    function init(tideDom, weatherDom) {
        tideChart = echarts.init(document.getElementById(tideDom), null, { renderer: 'canvas' });
        weatherChart = echarts.init(document.getElementById(weatherDom), null, { renderer: 'canvas' });
        window.addEventListener('resize', () => {
            tideChart && tideChart.resize();
            weatherChart && weatherChart.resize();
        });
        tideChart.setOption(tideBaseOption());
        weatherChart.setOption(weatherBaseOption());
    }

    function labelOf(w) {
        if (!w) return '';
        const d = new Date(w.replace(' ', 'T'));
        const p = n => String(n).padStart(2, '0');
        return `${p(d.getMonth() + 1)}-${p(d.getDate())}\n${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    function tideBaseOption() {
        return {
            backgroundColor: 'transparent',
            grid: { left: 52, right: 52, top: 34, bottom: 44 },
            tooltip: {
                trigger: 'axis',
                backgroundColor: 'rgba(10,20,40,.92)',
                borderColor: 'rgba(88,148,255,.4)',
                textStyle: { color: '#e8f1ff', fontSize: 12 },
                axisPointer: { type: 'cross', label: { backgroundColor: '#1d3a63' } }
            },
            legend: {
                data: [S_BORROW, S_RETURN, S_NET, S_ETI],
                textStyle: { color: AXIS_COLOR, fontSize: 11 },
                top: 4, icon: 'roundRect', itemWidth: 12, itemHeight: 6
            },
            xAxis: {
                type: 'category',
                data: [],
                axisLine: { lineStyle: { color: SPLIT_COLOR } },
                axisLabel: { color: AXIS_COLOR, fontSize: 10, lineHeight: 13, interval: 'auto' },
                axisTick: { show: false }
            },
            yAxis: [
                {
                    type: 'value', name: 'Events',
                    nameTextStyle: { color: AXIS_COLOR, fontSize: 10 },
                    axisLabel: { color: AXIS_COLOR, fontSize: 10 },
                    splitLine: { lineStyle: { color: SPLIT_COLOR } }
                },
                {
                    type: 'value', name: 'E(t) %', min: 0,
                    nameTextStyle: { color: AXIS_COLOR, fontSize: 10 },
                    axisLabel: { color: AXIS_COLOR, fontSize: 10, formatter: '{value}%' },
                    splitLine: { show: false }
                }
            ],
            series: []
        };
    }

    function weatherBaseOption() {
        return {
            backgroundColor: 'transparent',
            grid: { left: 46, right: 14, top: 26, bottom: 30 },
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                backgroundColor: 'rgba(10,20,40,.92)', borderColor: 'rgba(88,148,255,.4)',
                textStyle: { color: '#e8f1ff', fontSize: 12 }
            },
            legend: { data: [W_BORROW, W_RETURN], textStyle: { color: AXIS_COLOR, fontSize: 10 }, top: 0, itemWidth: 10, itemHeight: 6 },
            xAxis: { type: 'category', data: [], axisLabel: { color: AXIS_COLOR, fontSize: 10, interval: 0, rotate: 20 }, axisTick: { show: false }, axisLine: { lineStyle: { color: SPLIT_COLOR } } },
            yAxis: { type: 'value', axisLabel: { color: AXIS_COLOR, fontSize: 10 }, splitLine: { lineStyle: { color: SPLIT_COLOR } } },
            series: [
                { name: W_BORROW, type: 'bar', data: [], itemStyle: { color: '#ffc552', borderRadius: [3, 3, 0, 0] }, barWidth: 10 },
                { name: W_RETURN, type: 'bar', data: [], itemStyle: { color: '#37d99a', borderRadius: [3, 3, 0, 0] }, barWidth: 10 }
            ]
        };
    }

    /** Citywide tidal time series */
    function setSeries(rows) {
        if (!tideChart) return;
        seriesData = rows || [];
        const labels = seriesData.map(r => labelOf(r.window_start));
        const borrow = seriesData.map(r => r.borrow_total);
        const ret = seriesData.map(r => r.return_total);
        const net = seriesData.map(r => r.net_flow);
        const eti = seriesData.map(r => r.e_index);

        tideChart.setOption({
            xAxis: { data: labels },
            series: [
                {
                    name: S_BORROW, type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
                    data: borrow, lineStyle: { width: 2, color: '#ffc552' },
                    itemStyle: { color: '#ffc552' },
                    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(255,197,82,.28)' }, { offset: 1, color: 'rgba(255,197,82,0)' }]) }
                },
                {
                    name: S_RETURN, type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
                    data: ret, lineStyle: { width: 2, color: '#37d99a' },
                    itemStyle: { color: '#37d99a' },
                    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(55,217,154,.26)' }, { offset: 1, color: 'rgba(55,217,154,0)' }]) }
                },
                {
                    name: S_NET, type: 'bar', data: net, barWidth: 6,
                    itemStyle: {
                        borderRadius: [2, 2, 0, 0],
                        color: (p) => p.value >= 0 ? 'rgba(255,77,94,.7)' : 'rgba(62,160,255,.7)'
                    }
                },
                {
                    name: S_ETI, type: 'line', yAxisIndex: 1, smooth: true,
                    symbol: 'none', data: eti,
                    lineStyle: { width: 2, type: 'dashed', color: '#a77bff' },
                    itemStyle: { color: '#a77bff' }
                }
            ]
        }, { replaceMerge: ['series'] });
    }

    /** Marker line for the currently displayed window */
    function markCurrent(windowStart) {
        if (!tideChart || !windowStart) return;
        const idx = seriesData.findIndex(r => r.window_start === windowStart);
        if (idx < 0) return;
        tideChart.setOption({
            series: [
                { name: S_BORROW, markLine: silentLine(idx) },
                {}, {}, {}
            ]
        }, { replaceMerge: [] });
    }

    function silentLine(idx) {
        return {
            silent: true, symbol: 'none',
            label: { show: false },
            lineStyle: { color: '#33b6ff', width: 1.5, type: 'solid', opacity: .85 },
            data: [{ xAxis: idx }]
        };
    }

    /** Weather effect comparison (aggregated by weather type) */
    function setWeatherCompare(rows) {
        if (!weatherChart) return;
        const agg = {};
        const order = [];
        (rows || []).forEach(r => {
            // weather_type comes back in Chinese; translate for the axis label
            const k = I18N.weather(r.weather_type);
            if (!(k in agg)) {
                agg[k] = { b: 0, r: 0 };
                order.push(k);
            }
            agg[k].b += r.total_borrow || 0;
            agg[k].r += r.total_return || 0;
        });
        weatherChart.setOption({
            xAxis: { data: order },
            series: [
                { name: W_BORROW, data: order.map(k => agg[k].b) },
                { name: W_RETURN, data: order.map(k => agg[k].r) }
            ]
        });
    }

    return { init, setSeries, setWeatherCompare, markCurrent };
})();

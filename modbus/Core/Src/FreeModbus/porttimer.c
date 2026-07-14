#include "port.h"
#include "mb.h"
#include "mbport.h"

static uint32_t ulTimerPeriodUs = 0U;

BOOL xMBPortTimersInit(USHORT usTimeOut50us)
{
    /*
     * FreeModbus 传入单位：50 μs。
     * TIM3 已配置为 1 MHz，因此 1 tick = 1 μs。
     */
    ulTimerPeriodUs = (uint32_t)usTimeOut50us * 50U;

    if ((ulTimerPeriodUs == 0U) ||
        (ulTimerPeriodUs > 65536U))
    {
        return FALSE;
    }

    __HAL_TIM_DISABLE(&htim3);
    __HAL_TIM_DISABLE_IT(&htim3, TIM_IT_UPDATE);

    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    __HAL_TIM_SET_AUTORELOAD(&htim3, ulTimerPeriodUs - 1U);

    __HAL_TIM_CLEAR_FLAG(&htim3, TIM_FLAG_UPDATE);

    return TRUE;
}

void vMBPortTimersEnable(void)
{
    /*
     * 每收到一个新字节，FreeModbus 都会重新启动 t3.5。
     */
    __HAL_TIM_DISABLE(&htim3);
    __HAL_TIM_DISABLE_IT(&htim3, TIM_IT_UPDATE);

    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    __HAL_TIM_CLEAR_FLAG(&htim3, TIM_FLAG_UPDATE);

    __HAL_TIM_ENABLE_IT(&htim3, TIM_IT_UPDATE);
    __HAL_TIM_ENABLE(&htim3);
}

void vMBPortTimersDisable(void)
{
    __HAL_TIM_DISABLE_IT(&htim3, TIM_IT_UPDATE);
    __HAL_TIM_DISABLE(&htim3);

    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    __HAL_TIM_CLEAR_FLAG(&htim3, TIM_FLAG_UPDATE);
}

void xMBPortTimersClose(void)
{
    vMBPortTimersDisable();
}

void vMBPortTimersDelay(USHORT usTimeOutMS)
{
    HAL_Delay(usTimeOutMS);
}

void TIMERExpiredISR(void)
{
    if (pxMBPortCBTimerExpired != NULL)
    {
        (void)pxMBPortCBTimerExpired();
    }
}

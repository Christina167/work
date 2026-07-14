#include "port.h"
#include "mb.h"
#include "mbport.h"

static USHORT usTimerTimeout50us = 0;

BOOL xMBPortTimersInit(USHORT usTim1Timerout50us)
{
    usTimerTimeout50us = usTim1Timerout50us;

    /*
     * TIM3 配置为 1 MHz：
     * 1 tick = 1 us
     * FreeModbus 的单位是 50 us
     */
    __HAL_TIM_SET_PRESCALER(&htim3, 72 - 1);
    __HAL_TIM_SET_AUTORELOAD(&htim3, usTimerTimeout50us * 50 - 1);
    __HAL_TIM_SET_COUNTER(&htim3, 0);

    return TRUE;
}

void vMBPortTimersEnable(void)
{
    __HAL_TIM_SET_AUTORELOAD(&htim3, usTimerTimeout50us * 50 - 1);
    __HAL_TIM_SET_COUNTER(&htim3, 0);
    __HAL_TIM_CLEAR_FLAG(&htim3, TIM_FLAG_UPDATE);
    HAL_TIM_Base_Start_IT(&htim3);
}

void vMBPortTimersDisable(void)
{
    HAL_TIM_Base_Stop_IT(&htim3);
    __HAL_TIM_SET_COUNTER(&htim3, 0);
}

void TIMERExpiredISR(void)
{
    (void)pxMBPortCBTimerExpired();
}
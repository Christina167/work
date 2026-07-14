#include "port.h"
#include "mb.h"
#include "mbport.h"

BOOL xMBPortSerialInit(UCHAR ucPORT,
                       ULONG ulBaudRate,
                       UCHAR ucDataBits,
                       eMBParity eParity)
{
    /*
     * 串口参数由 CubeMX 配置。
     * 阶段 1 固定使用 USART1：9600, 8N1。
     */
    (void)ucPORT;
    (void)ulBaudRate;
    (void)ucDataBits;
    (void)eParity;

    __HAL_UART_ENABLE_IT(&huart1, UART_IT_RXNE);
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);

    return TRUE;
}

void vMBPortSerialEnable(BOOL xRxEnable, BOOL xTxEnable)
{
    if (xRxEnable)
    {
        __HAL_UART_ENABLE_IT(&huart1, UART_IT_RXNE);
    }
    else
    {
        __HAL_UART_DISABLE_IT(&huart1, UART_IT_RXNE);
    }

    if (xTxEnable)
    {
        __HAL_UART_ENABLE_IT(&huart1, UART_IT_TXE);
    }
    else
    {
        __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);
    }
}

BOOL xMBPortSerialPutByte(CHAR ucByte)
{
    huart1.Instance->DR = (uint8_t)ucByte;
    return TRUE;
}

BOOL xMBPortSerialGetByte(CHAR *pucByte)
{
    *pucByte = (CHAR)(huart1.Instance->DR & 0xFF);
    return TRUE;
}

void vMBPortClose(void)
{
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_RXNE);
    __HAL_UART_DISABLE_IT(&huart1, UART_IT_TXE);
}